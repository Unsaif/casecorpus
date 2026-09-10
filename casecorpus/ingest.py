"""Ingest extraction outputs from work/<pmid>/ : ground -> validate -> phenopacket -> catalogue + records/."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Catalogue
from .extract.runner import slug
from .ground import Grounders
from .phenopacket import to_phenopacket, validate_phenopacket
from .validate import confidence_from_issues, validate_manifest, validate_record


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
        for rp in sorted(wd.glob("record_*.json")):
            rec = json.load(open(rp))
            rec = grounders.ground_record(rec)
            issues = validate_record(rec, grounders)
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
            stats["records"] += 1
            stats["valid" if valid else "invalid"] += 1
    return stats
