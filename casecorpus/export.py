"""Flat relational export (parquet + csv) for analysis: documents, individuals, phenotypes,
measurements, enzyme_activities, genetic_findings, diagnoses, treatments, links."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .config import Settings
from .db import Catalogue


def export_all(settings: Settings, cat: Catalogue, fmt: str = "both") -> dict[str, int]:
    settings.ensure()
    out = settings.export_dir
    tables: dict[str, list[dict[str, Any]]] = {k: [] for k in ("documents", "individuals", "phenotypes", "measurements", "enzyme_activities", "genetic_findings", "diagnoses", "treatments", "links")}
    for d in cat.iter_documents():
        tables["documents"].append({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in d.items() if k not in ("abstract",)})
    for r in cat.iter_individuals():
        rec = r["record"]
        subj = rec.get("subject", {})
        tables["individuals"].append({
            "record_id": r["record_id"], "pmid": r["pmid"], "local_id": r["local_id"], "valid": r["valid"], "confidence": r["confidence"],
            "sex": subj.get("sex"), "age_at_onset": (subj.get("age_at_onset") or {}).get("iso8601"), "age_at_presentation": (subj.get("age_at_presentation") or {}).get("iso8601"),
            "age_at_last_followup": (subj.get("age_at_last_followup") or {}).get("iso8601"), "vital_status": subj.get("vital_status"),
            "consanguinity": (rec.get("family") or {}).get("consanguinity"), "ancestry": subj.get("ancestry_as_stated"), "country": subj.get("country_of_care"),
            "final_diagnosis": next((d.get("disease_text") for d in rec.get("diagnoses", []) if d.get("status") == "FINAL"), None),
            "final_diagnosis_mondo": next(((d.get("disease") or {}).get("mondo") for d in rec.get("diagnoses", []) if d.get("status") == "FINAL"), None),
            "final_diagnosis_omim": next(((d.get("disease") or {}).get("omim") for d in rec.get("diagnoses", []) if d.get("status") == "FINAL"), None),
            "confidence_tier": next((d.get("confidence_tier") for d in rec.get("diagnoses", []) if d.get("status") == "FINAL"), None),
            "n_phenotypes": len(rec.get("phenotypes", [])), "n_measurements": len(rec.get("measurements", [])), "n_variants": len(rec.get("genetic_findings", [])),
            "clinical_summary": rec.get("clinical_summary"), "model": r["model"], "prompt_version": r["prompt_version"],
        })
        for ph in rec.get("phenotypes", []):
            tables["phenotypes"].append({"record_id": r["record_id"], "pmid": r["pmid"], "text": ph.get("text"), "hpo_id": ph.get("hpo_id"), "hpo_label": ph.get("hpo_label"),
                                         "excluded": ph.get("excluded"), "onset": (ph.get("onset") or {}).get("iso8601"), "modifier": ph.get("modifier"),
                                         "grounding": (ph.get("grounding") or {}).get("status"), "quote": (ph.get("evidence") or {}).get("quote"), "anchor": (ph.get("evidence") or {}).get("anchor")})
        for m in rec.get("measurements", []):
            an = m.get("analyte") or {}
            rr = m.get("reference_range") or {}
            tables["measurements"].append({"record_id": r["record_id"], "pmid": r["pmid"], "analyte_text": m.get("analyte_text"), "analyte_label": an.get("label"), "chebi": an.get("chebi"), "vmh": an.get("vmh"),
                                           "specimen": m.get("specimen"), "value": m.get("value"), "value_text": m.get("value_text"), "unit": m.get("unit"), "unit_ucum": m.get("unit_ucum"),
                                           "ref_low": rr.get("low"), "ref_high": rr.get("high"), "ref_text": rr.get("text"), "interpretation": m.get("interpretation"),
                                           "timing": m.get("timing"), "condition": m.get("condition"), "age": (m.get("age") or {}).get("iso8601"), "panel": m.get("panel"), "method": m.get("method"),
                                           "grounding": (m.get("grounding") or {}).get("status"), "quote": (m.get("evidence") or {}).get("quote"), "anchor": (m.get("evidence") or {}).get("anchor")})
        for e in rec.get("enzyme_activities", []):
            tables["enzyme_activities"].append({"record_id": r["record_id"], "pmid": r["pmid"], **{k: e.get(k) for k in ("enzyme_text", "gene_symbol", "ec_number", "tissue", "value", "unit", "percent_of_control", "reference_text", "interpretation")},
                                                "quote": (e.get("evidence") or {}).get("quote"), "anchor": (e.get("evidence") or {}).get("anchor")})
        for g in rec.get("genetic_findings", []):
            tables["genetic_findings"].append({"record_id": r["record_id"], "pmid": r["pmid"], **{k: g.get(k) for k in ("gene_symbol", "hgnc_id", "variant_hgvs_c", "variant_hgvs_p", "variant_hgvs_g", "transcript", "variant_text", "zygosity", "inheritance_as_stated", "classification_as_reported", "method", "novel")},
                                               "grounding": (g.get("grounding") or {}).get("status"), "quote": (g.get("evidence") or {}).get("quote"), "anchor": (g.get("evidence") or {}).get("anchor")})
        for d in rec.get("diagnoses", []):
            dis = d.get("disease") or {}
            tables["diagnoses"].append({"record_id": r["record_id"], "pmid": r["pmid"], "disease_text": d.get("disease_text"), "label": dis.get("label"), "mondo": dis.get("mondo"), "omim": dis.get("omim"), "orphanet": dis.get("orphanet"),
                                        "status": d.get("status"), "confidence_tier": d.get("confidence_tier"), "diagnostic_route": d.get("diagnostic_route"), "age_at_diagnosis": (d.get("age_at_diagnosis") or {}).get("iso8601"),
                                        "time_to_diagnosis_text": d.get("time_to_diagnosis_text"), "prior_misdiagnoses": "|".join(d.get("prior_misdiagnoses") or []), "grounding": (d.get("grounding") or {}).get("status"),
                                        "quote": (d.get("evidence") or {}).get("quote"), "anchor": (d.get("evidence") or {}).get("anchor")})
        for t in rec.get("treatments", []):
            tables["treatments"].append({"record_id": r["record_id"], "pmid": r["pmid"], **{k: t.get(k) for k in ("text", "type", "agent", "response", "response_text")},
                                         "quote": (t.get("evidence") or {}).get("quote"), "anchor": (t.get("evidence") or {}).get("anchor")})
    for row in cat.conn.execute("SELECT * FROM links"):
        tables["links"].append(dict(row))
    counts = {}
    for name, rows in tables.items():
        df = pd.DataFrame(rows)
        counts[name] = len(df)
        if fmt in ("csv", "both"):
            df.to_csv(out / f"{name}.csv", index=False)
        if fmt in ("parquet", "both") and len(df):
            df.to_parquet(out / f"{name}.parquet", index=False)
    return counts
