"""Ingest extraction outputs from work/<pmid>/ : ground -> validate -> phenopacket -> catalogue + records/."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Catalogue
from .anchors import assemble_narrative, index_markdown, verify_quote
from .casesheet import write_case_sheet
from .extract.runner import slug
from .ground import Grounders
from .phenopacket import to_phenopacket, validate_phenopacket
from .validate import confidence_from_issues, validate_manifest, validate_record


_GENERIC_SECTIONS = re.compile(r"^(introduction|background|discussion|conclusion|conclusions|literature review|review of the literature|methods|materials and methods)\b", re.I)
_SELF_REF = re.compile(r"\b(our patient|the patient|this patient|the proband|our proband|this case|our case|the present case|the index case|the child|the boy|the girl|the infant|the neonate|the fetus|the family|the siblings)\b", re.I)


def backfill_anchors(rec: dict[str, Any], where: list[str], idx) -> list[str]:
    """Coarse narrative from the triage manifest's 'where' list when the extractor gave no narrative_anchors."""
    out: list[str] = []
    if idx.abstract:
        out.append("abstract")
    local_id = (rec.get("local_id") or "").strip()
    for a in where:
        m = re.match(r"^sec:(\d+)$", a.strip(), re.I)
        if m:
            n = int(m.group(1))
            sec = idx.sections.get(n)
            if not sec:
                continue
            if _GENERIC_SECTIONS.match(sec.get("title", "")):
                for k in sorted(sec["paragraphs"]):
                    t = sec["paragraphs"][k]
                    if _SELF_REF.search(t) or (local_id and local_id.lower() in t.lower()):
                        out.append(f"sec:{n} ¶{k}")
            else:
                out.append(a)
        else:
            out.append(a)
    # any evidence anchors used by the record that are not yet covered
    for section in ("phenotypes", "measurements", "enzyme_activities", "genetic_findings", "diagnoses", "treatments"):
        for item in rec.get(section, []):
            an = ((item.get("evidence") or {}).get("anchor") or "").strip()
            mm = re.match(r"^(sec:\d+ ¶\d+|tab:\d+ r\d+)", an)
            if mm and mm.group(1) not in out:
                out.append(mm.group(1))
    return out


def ingest(settings: Settings, cat: Catalogue, grounders: Grounders | None = None, pmids: list[str] | None = None) -> dict[str, Any]:
    settings.ensure()
    grounders = grounders or Grounders(settings.ontologies, settings.scope_dir)
    stats = {"manifests": 0, "in_scope": 0, "records": 0, "valid": 0, "invalid": 0, "manifest_errors": 0}
    dirs = [settings.work_dir / p for p in pmids] if pmids else sorted(p for p in settings.work_dir.iterdir() if p.is_dir())
    for wd in dirs:
        pmid = wd.name
        mpath = wd / "manifest.json"
        if not mpath.exists():
            continue
        manifest = json.load(open(mpath))
        merrs = validate_manifest(manifest)
        if merrs:
            stats["manifest_errors"] += 1
            (wd / "manifest.issues.json").write_text(json.dumps(merrs, indent=1))
        stats["manifests"] += 1
        cat.set_triage(pmid, in_scope=int(bool(manifest.get("in_scope"))), n_individuals=len([i for i in manifest.get("individuals", []) if i.get("is_affected", True)]),
                       individual_ids=[i["local_id"] for i in manifest.get("individuals", [])], is_rereport=int(any(i.get("is_rereport") for i in manifest.get("individuals", []))),
                       reasons=manifest.get("reasons"), model=manifest.get("_model"), prompt_version=manifest.get("_prompt_version"))
        if not manifest.get("in_scope"):
            continue
        stats["in_scope"] += 1
        doc = cat.get_document(pmid) or {}
        md = (wd / "input.md").read_text() if (wd / "input.md").exists() else ""
        idx = index_markdown(md)
        manifest_where = {i.get("local_id"): i.get("where", []) for i in manifest.get("individuals", [])}
        for rp in sorted(wd.glob("record_*.json")):
            rec = json.load(open(rp))
            # --- full patient description: verbatim passages assembled from the source by anchor ---
            anchors = rec.get("narrative_anchors") or []
            source = "extractor"
            if not anchors:
                # backfill from the triage manifest's coarse 'where' anchors (whole sections / tables):
                # keep case-level sections whole, but from Introduction/Background/Discussion/Conclusion keep only
                # paragraphs that refer to this individual.
                anchors = backfill_anchors(rec, manifest_where.get(rec.get("local_id"), []), idx)
                source = "manifest"
                rec["narrative_anchors"] = anchors
            rec["case_narrative"] = assemble_narrative(anchors, idx)
            for unit in rec["case_narrative"]:
                unit["source"] = source
            rec["narrative_source"] = source
            # --- verify every evidence quote against its anchor and the document ---
            nv = {"total": 0, "in_anchor": 0, "in_document": 0, "missing": 0}
            for section in ("phenotypes", "measurements", "enzyme_activities", "genetic_findings", "diagnoses", "treatments"):
                for item in rec.get(section, []):
                    ev = item.get("evidence")
                    if not ev:
                        continue
                    v = verify_quote(ev.get("quote", ""), ev.get("anchor", ""), idx, md)
                    ev["verification"] = v
                    nv["total"] += 1
                    nv["in_anchor"] += int(v["in_anchor"])
                    nv["in_document"] += int(v["in_document"])
                    nv["missing"] += int(not v["in_document"])
            rec["_evidence_verification"] = nv
            rec = grounders.ground_record(rec)
            issues = validate_record(rec, grounders)
            if nv["missing"]:
                issues.append({"severity": "warn", "path": "evidence", "msg": f"{nv['missing']} of {nv['total']} evidence quotes not found verbatim in the document"})
            if not rec["case_narrative"]:
                issues.append({"severity": "warn", "path": "narrative_anchors", "msg": "no narrative could be assembled (anchors missing or unresolvable)"})
            record_id = f"PMID_{pmid}_{slug(rec.get('local_id', rp.stem[7:]))}"
            pp = to_phenopacket(rec, pmid, record_id, doc.get("title"))
            pp_issues = validate_phenopacket(pp)
            for m in pp_issues:
                issues.append({"severity": "error" if "not installed" not in m else "info", "path": "phenopacket", "msg": m})
            valid = not any(i["severity"] == "error" for i in issues)
            conf = confidence_from_issues(rec, issues)
            rec["_source"] = {"pmid": pmid, "pmcid": doc.get("pmcid"), "doi": doc.get("doi"), "title": doc.get("title"), "journal": doc.get("journal"),
                              "year": doc.get("pub_year"), "license": doc.get("license"), "tier": doc.get("fulltext_tier")}
            rec["_validation"] = issues
            rec["_confidence"] = conf
            cat.put_individual(record_id, pmid, rec.get("local_id"), rec, pp if valid else None, valid, issues, conf,
                               rec.get("_model", "unknown"), rec.get("_prompt_version", "unknown"))
            out = settings.records_dir / pmid
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{record_id}.record.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))
            if valid:
                (out / f"{record_id}.phenopacket.json").write_text(json.dumps(pp, indent=1, ensure_ascii=False))
            write_case_sheet(rec, record_id, out / f"{record_id}.md")
            stats["records"] += 1
            stats["valid" if valid else "invalid"] += 1
    return stats
