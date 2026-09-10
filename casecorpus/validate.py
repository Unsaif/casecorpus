"""Record validation: schema, identifiers, internal consistency, plausibility.
Returns a list of issues; severity 'error' blocks the record, 'warn' is kept for curators."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
RECORD_SCHEMA = json.load(open(SCHEMA_DIR / "record.schema.json"))
MANIFEST_SCHEMA = json.load(open(SCHEMA_DIR / "manifest.schema.json"))
_validator = jsonschema.Draft202012Validator(RECORD_SCHEMA)
_mvalidator = jsonschema.Draft202012Validator(MANIFEST_SCHEMA)

ISO_DUR = re.compile(r"^P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(\d+H)?(\d+M)?(\d+S)?)?$")
HGVS_C = re.compile(r"^c\.[\d_+\-*]+[ACGTacgt]*(>|del|dup|ins|inv|delins)?[ACGTacgt]*$|^c\.[\d_+\-*]+(del|dup|ins|inv|delins)[ACGTacgt]*\d*$")
HGVS_P = re.compile(r"^p\.\(?[A-Z][a-z]{2}\d+(?:[A-Z][a-z]{2}|\*|=|Ter|fs.*|del.*|dup.*|ins.*)?\)?$|^p\.\(?[A-Z]\d+[A-Z*=]\)?$")

X_LINKED_HINT = re.compile(r"x-linked", re.I)


def iso_to_days(iso: str | None) -> float | None:
    if not iso or not ISO_DUR.match(iso):
        return None
    m = ISO_DUR.match(iso)
    y, mo, w, d = (int(x[:-1]) if x else 0 for x in m.groups()[:4])
    return y * 365.25 + mo * 30.44 + w * 7 + d


def _strip_private(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def validate_manifest(m: dict[str, Any]) -> list[dict[str, str]]:
    return [{"severity": "error", "path": "/".join(str(p) for p in e.path), "msg": e.message[:300]} for e in _mvalidator.iter_errors(_strip_private(m))]


def validate_record(rec: dict[str, Any], grounders: Any | None = None) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for e in _validator.iter_errors(_strip_private(rec)):
        issues.append({"severity": "error", "path": "/".join(str(p) for p in e.path), "msg": e.message[:300]})

    subj = rec.get("subject", {})
    ages = {k: iso_to_days((subj.get(k) or {}).get("iso8601")) for k in ("age_at_onset", "age_at_presentation", "age_at_last_followup", "age_at_death")}
    for k, v in subj.items():
        if isinstance(v, dict) and v.get("iso8601") and not ISO_DUR.match(v["iso8601"]):
            issues.append({"severity": "error", "path": f"subject/{k}/iso8601", "msg": f"not an ISO 8601 duration: {v['iso8601']}"})
    if ages["age_at_onset"] is not None and ages["age_at_presentation"] is not None and ages["age_at_onset"] > ages["age_at_presentation"] + 1:
        issues.append({"severity": "warn", "path": "subject/age_at_onset", "msg": "onset after presentation"})
    if ages["age_at_death"] is not None and ages["age_at_last_followup"] is not None and ages["age_at_death"] < ages["age_at_last_followup"] - 1:
        issues.append({"severity": "warn", "path": "subject/age_at_death", "msg": "death before last follow-up"})
    if subj.get("vital_status") == "DECEASED" and not subj.get("age_at_death"):
        issues.append({"severity": "info", "path": "subject/age_at_death", "msg": "deceased without age at death"})
    if any(a is not None and a > 120 * 365.25 for a in ages.values()):
        issues.append({"severity": "error", "path": "subject", "msg": "age > 120 years"})

    for i, ph in enumerate(rec.get("phenotypes", [])):
        if ph.get("hpo_id") and not re.match(r"^HP:\d{7}$", ph["hpo_id"]):
            issues.append({"severity": "error", "path": f"phenotypes/{i}/hpo_id", "msg": "malformed HPO id"})
        if grounders and ph.get("hpo_id") and ph["hpo_id"] not in grounders.hpo.label:
            issues.append({"severity": "error", "path": f"phenotypes/{i}/hpo_id", "msg": f"unknown/obsolete HPO id {ph['hpo_id']}"})
        if not (ph.get("evidence") or {}).get("quote"):
            issues.append({"severity": "warn", "path": f"phenotypes/{i}/evidence", "msg": "no evidence quote"})

    for i, m in enumerate(rec.get("measurements", [])):
        if m.get("value") is not None and not m.get("unit") and m.get("interpretation") != "UNKNOWN":
            issues.append({"severity": "warn", "path": f"measurements/{i}/unit", "msg": "numeric value without unit"})
        rr = m.get("reference_range") or {}
        if m.get("value") is not None and rr.get("high") is not None and rr.get("low") is not None:
            v = m["value"]
            expected = "HIGH" if v > rr["high"] else "LOW" if v < rr["low"] else "NORMAL"
            if m.get("interpretation") in ("HIGH", "LOW", "NORMAL") and m["interpretation"] != expected:
                issues.append({"severity": "warn", "path": f"measurements/{i}/interpretation", "msg": f"interpretation {m['interpretation']} disagrees with reference range ({expected})"})
        if m.get("value") is not None and m["value"] < 0 and (m.get("unit_ucum") or "") not in ("mm[Hg]",):
            issues.append({"severity": "warn", "path": f"measurements/{i}/value", "msg": "negative value"})
        if not (m.get("evidence") or {}).get("quote"):
            issues.append({"severity": "warn", "path": f"measurements/{i}/evidence", "msg": "no evidence quote"})

    for i, g in enumerate(rec.get("genetic_findings", [])):
        c = g.get("variant_hgvs_c")
        if c and not HGVS_C.match(c.strip()):
            issues.append({"severity": "warn", "path": f"genetic_findings/{i}/variant_hgvs_c", "msg": f"HGVS c. not recognised: {c}"})
        p = g.get("variant_hgvs_p")
        if p and not HGVS_P.match(p.strip()):
            issues.append({"severity": "warn", "path": f"genetic_findings/{i}/variant_hgvs_p", "msg": f"HGVS p. not recognised: {p}"})
        if g.get("zygosity") == "HEMIZYGOUS" and subj.get("sex") == "FEMALE":
            issues.append({"severity": "warn", "path": f"genetic_findings/{i}/zygosity", "msg": "hemizygous in a female"})
        if grounders and (g.get("grounding") or {}).get("status") == "unresolved":
            issues.append({"severity": "warn", "path": f"genetic_findings/{i}/gene_symbol", "msg": f"gene symbol not recognised: {g.get('gene_symbol')}"})

    finals = [d for d in rec.get("diagnoses", []) if d.get("status") == "FINAL"]
    if not finals:
        issues.append({"severity": "warn", "path": "diagnoses", "msg": "no FINAL diagnosis"})
    for i, d in enumerate(rec.get("diagnoses", [])):
        if d.get("confidence_tier") == "MOLECULAR" and not rec.get("genetic_findings"):
            issues.append({"severity": "warn", "path": f"diagnoses/{i}/confidence_tier", "msg": "MOLECULAR tier without genetic findings"})
        if d.get("confidence_tier") == "ENZYMATIC" and not rec.get("enzyme_activities"):
            issues.append({"severity": "warn", "path": f"diagnoses/{i}/confidence_tier", "msg": "ENZYMATIC tier without enzyme activities"})
    return issues


def confidence_from_issues(rec: dict[str, Any], issues: list[dict[str, str]]) -> float:
    """Crude per-record confidence: grounded fraction minus penalties for warnings."""
    items = rec.get("phenotypes", []) + rec.get("measurements", []) + rec.get("genetic_findings", []) + rec.get("diagnoses", [])
    if not items:
        return 0.0
    grounded = sum(1 for it in items if (it.get("grounding") or {}).get("status") == "grounded")
    base = grounded / len(items)
    warns = sum(1 for i in issues if i["severity"] == "warn")
    return round(max(0.0, base - 0.03 * warns), 3)
