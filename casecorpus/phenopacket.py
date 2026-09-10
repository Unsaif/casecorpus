"""CaseRecord -> GA4GH Phenopacket v2 (JSON). Everything the phenopacket cannot hold stays in the
CaseRecord (the 'sidecar'); the phenopacket carries an external reference back to it.
Validated by round-tripping through the official protobuf classes."""
from __future__ import annotations

import datetime as dt
from typing import Any

from . import __version__

_ZYG = {
    "HOMOZYGOUS": ("GENO:0000136", "homozygous"),
    "HETEROZYGOUS": ("GENO:0000135", "heterozygous"),
    "COMPOUND_HETEROZYGOUS": ("GENO:0000402", "compound heterozygous"),
    "HEMIZYGOUS": ("GENO:0000134", "hemizygous"),
    "MOSAIC": ("GENO:0000135", "heterozygous"),
    "HOMOPLASMIC": ("GENO:0000602", "homoplasmic"),
    "HETEROPLASMIC": ("GENO:0000603", "heteroplasmic"),
}
_ACMG = {"pathogenic": "PATHOGENIC", "likely_pathogenic": "LIKELY_PATHOGENIC", "uncertain_significance": "UNCERTAIN_SIGNIFICANCE",
         "likely_benign": "LIKELY_BENIGN", "benign": "BENIGN"}
_INTERP = {"HIGH": "HIGH", "LOW": "LOW", "NORMAL": "NORMAL", "ABNORMAL": "ABNORMAL", "UNKNOWN": "UNKNOWN"}


def _age(a: dict[str, Any] | None) -> dict[str, Any] | None:
    if a and a.get("iso8601"):
        return {"age": {"iso8601duration": a["iso8601"]}}
    return None


def to_phenopacket(rec: dict[str, Any], pmid: str, record_id: str, title: str | None = None) -> dict[str, Any]:
    subj = rec.get("subject", {})
    pp: dict[str, Any] = {
        "id": record_id,
        "subject": {"id": rec.get("local_id", "individual"), "sex": subj.get("sex", "UNKNOWN_SEX")},
        "phenotypicFeatures": [],
        "measurements": [],
        "interpretations": [],
        "diseases": [],
        "medicalActions": [],
        "metaData": {
            "created": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "createdBy": f"casecorpus {__version__}",
            "resources": [
                {"id": "hp", "name": "human phenotype ontology", "url": "http://purl.obolibrary.org/obo/hp.owl", "version": "", "namespacePrefix": "HP", "iriPrefix": "http://purl.obolibrary.org/obo/HP_"},
                {"id": "mondo", "name": "Mondo Disease Ontology", "url": "http://purl.obolibrary.org/obo/mondo.owl", "version": "", "namespacePrefix": "MONDO", "iriPrefix": "http://purl.obolibrary.org/obo/MONDO_"},
                {"id": "omim", "name": "Online Mendelian Inheritance in Man", "url": "https://www.omim.org", "version": "", "namespacePrefix": "OMIM", "iriPrefix": "https://omim.org/entry/"},
                {"id": "geno", "name": "Genotype Ontology", "url": "http://purl.obolibrary.org/obo/geno.owl", "version": "", "namespacePrefix": "GENO", "iriPrefix": "http://purl.obolibrary.org/obo/GENO_"},
                {"id": "chebi", "name": "Chemical Entities of Biological Interest", "url": "http://purl.obolibrary.org/obo/chebi.owl", "version": "", "namespacePrefix": "CHEBI", "iriPrefix": "http://purl.obolibrary.org/obo/CHEBI_"},
                {"id": "hgnc", "name": "HUGO Gene Nomenclature Committee", "url": "https://www.genenames.org", "version": "", "namespacePrefix": "HGNC", "iriPrefix": "https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/"},
            ],
            "phenopacketSchemaVersion": "2.0",
            "externalReferences": [{"id": f"PMID:{pmid}", "reference": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}", "description": title or ""}],
        },
    }
    last = _age(subj.get("age_at_last_followup")) or _age(subj.get("age_at_presentation"))
    if last:
        pp["subject"]["timeAtLastEncounter"] = last
    if subj.get("vital_status") in ("ALIVE", "DECEASED"):
        vs = {"status": subj["vital_status"]}
        if subj.get("age_at_death") and _age(subj["age_at_death"]):
            vs["timeOfDeath"] = _age(subj["age_at_death"])
        pp["subject"]["vitalStatus"] = vs

    for ph in rec.get("phenotypes", []):
        if not ph.get("hpo_id"):
            continue
        f: dict[str, Any] = {"type": {"id": ph["hpo_id"], "label": ph.get("hpo_label") or ph.get("text")}}
        if ph.get("excluded"):
            f["excluded"] = True
        if _age(ph.get("onset")):
            f["onset"] = _age(ph["onset"])
        if ph.get("evidence", {}).get("quote"):
            f["evidence"] = [{"evidenceCode": {"id": "ECO:0000033", "label": "author statement supported by traceable reference"},
                              "reference": {"id": f"PMID:{pmid}", "description": ph["evidence"]["quote"][:200]}}]
        pp["phenotypicFeatures"].append(f)

    for m in rec.get("measurements", []):
        an = m.get("analyte") or {}
        assay = {"id": an.get("chebi") or f"casecorpus:analyte/{(an.get('label') or m.get('analyte_text'))}", "label": an.get("label") or m.get("analyte_text")}
        meas: dict[str, Any] = {"assay": assay}
        if m.get("value") is not None:
            q: dict[str, Any] = {"unit": {"id": f"UCUM:{m.get('unit_ucum') or m.get('unit') or 'unknown'}", "label": m.get("unit") or "unknown"}, "value": m["value"]}
            rr = m.get("reference_range") or {}
            if rr.get("low") is not None and rr.get("high") is not None:
                q["referenceRange"] = {"unit": q["unit"], "low": rr["low"], "high": rr["high"]}
            meas["value"] = {"quantity": q}
        else:
            meas["value"] = {"ontologyClass": {"id": f"casecorpus:interpretation/{m.get('interpretation', 'UNKNOWN')}", "label": m.get("value_text") or m.get("interpretation", "UNKNOWN")}}
        if m.get("specimen"):
            meas["procedure"] = {"code": {"id": f"casecorpus:specimen/{m['specimen']}", "label": m["specimen"]}}
        if _age(m.get("age")):
            meas["timeObserved"] = _age(m["age"])
        desc = "; ".join(x for x in (m.get("timing"), m.get("condition"), m.get("panel")) if x)
        if desc:
            meas["description"] = desc
        pp["measurements"].append(meas)

    finals = [d for d in rec.get("diagnoses", []) if d.get("status") == "FINAL"]
    for d in rec.get("diagnoses", []):
        dis = d.get("disease") or {}
        did = dis.get("omim") or dis.get("mondo") or dis.get("orphanet")
        if not did or d.get("status") in ("DIFFERENTIAL", "EXCLUDED"):
            continue
        entry: dict[str, Any] = {"term": {"id": did, "label": dis.get("label") or d.get("disease_text")}}
        if _age(subj.get("age_at_onset")):
            entry["onset"] = _age(subj["age_at_onset"])
        if d.get("status") == "REVISED_FROM":
            entry["excluded"] = True
        pp["diseases"].append(entry)

    if finals or rec.get("genetic_findings"):
        d0 = finals[0] if finals else {}
        dis = d0.get("disease") or {}
        did = dis.get("omim") or dis.get("mondo") or dis.get("orphanet") or "casecorpus:diagnosis/unknown"
        interp: dict[str, Any] = {"id": f"{record_id}-interp", "progressStatus": "SOLVED" if finals and d0.get("confidence_tier") == "MOLECULAR" else "IN_PROGRESS",
                                  "diagnosis": {"disease": {"id": did, "label": dis.get("label") or d0.get("disease_text", "")}, "genomicInterpretations": []}}
        for g in rec.get("genetic_findings", []):
            vd: dict[str, Any] = {"id": f"{record_id}-{g.get('gene_symbol')}-{(g.get('variant_hgvs_c') or g.get('variant_text') or 'var')}".replace(" ", "_")[:120],
                                  "geneContext": {"valueId": g.get("hgnc_id") or f"HGNC:{g.get('gene_symbol')}", "symbol": g.get("gene_symbol")},
                                  "expressions": [], "moleculeContext": "unspecified_molecule_context"}
            tx = (g.get("transcript") + ":") if g.get("transcript") else ""
            if g.get("variant_hgvs_c"):
                vd["expressions"].append({"syntax": "hgvs.c", "value": tx + g["variant_hgvs_c"]})
            if g.get("variant_hgvs_p"):
                vd["expressions"].append({"syntax": "hgvs.p", "value": g["variant_hgvs_p"]})
            if g.get("variant_hgvs_g"):
                vd["expressions"].append({"syntax": "hgvs.g", "value": g["variant_hgvs_g"]})
            if not vd["expressions"] and g.get("variant_text"):
                vd["description"] = g["variant_text"]
            z = _ZYG.get(g.get("zygosity", ""))
            if z:
                vd["allelicState"] = {"id": z[0], "label": z[1]}
            vi: dict[str, Any] = {"variationDescriptor": vd, "acmgPathogenicityClassification": _ACMG.get(g.get("classification_as_reported") or "", "NOT_PROVIDED")}
            interp["diagnosis"]["genomicInterpretations"].append({"subjectOrBiosampleId": rec.get("local_id", "individual"),
                                                                  "interpretationStatus": "CAUSATIVE" if finals else "UNKNOWN_STATUS",
                                                                  "variantInterpretation": vi})
        pp["interpretations"].append(interp)

    for t in rec.get("treatments", []):
        pp["medicalActions"].append({"treatment": {"agent": {"id": f"casecorpus:treatment/{t.get('type')}", "label": t.get("agent") or t.get("text")}},
                                     "treatmentTarget": {"id": "casecorpus:diagnosis", "label": finals[0]["disease_text"] if finals else ""}})
    return {k: v for k, v in pp.items() if v not in ([], None)}


def validate_phenopacket(pp: dict[str, Any]) -> list[str]:
    """Round-trip through the official protobuf; returns a list of problems (empty = ok)."""
    try:
        from google.protobuf.json_format import Parse
        from phenopackets import Phenopacket
        import json as _json
        Parse(_json.dumps(pp), Phenopacket())
        return []
    except ImportError:
        return ["phenopackets package not installed; skipped structural validation"]
    except Exception as e:
        return [f"{type(e).__name__}: {str(e)[:300]}"]
