"""Validate the extraction files in work/<pmid>/ against the schemas and print issues.
Usage: python validate_work.py <work_dir>/<pmid>
Exit code 0 when everything is schema-valid."""
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from casecorpus.validate import validate_manifest, validate_record  # noqa: E402


def slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s[:40] or "individual"


wd = Path(sys.argv[1])
ok = True
m = wd / "manifest.json"
if not m.exists():
    print("MISSING manifest.json"); sys.exit(1)
manifest = json.load(open(m))
errs = validate_manifest(manifest)
for e in errs:
    print("MANIFEST", e["severity"], e["path"], e["msg"]); ok = False
if manifest.get("in_scope"):
    for ind in manifest.get("individuals", []):
        if not ind.get("is_affected", True):
            continue
        rp = wd / f"record_{slug(ind['local_id'])}.json"
        if not rp.exists():
            print("MISSING", rp.name, "for individual", ind["local_id"]); ok = False; continue
        rec = json.load(open(rp))
        issues = validate_record(rec)
        for i in issues:
            if i["severity"] == "error":
                ok = False
            print(rp.name, i["severity"], i["path"], i["msg"])
        # evidence quotes must occur in the source text (whitespace-insensitive)
        src = re.sub(r"\s+", " ", (wd / "input.md").read_text())
        n_missing = 0
        for section in ("phenotypes", "measurements", "genetic_findings", "diagnoses", "treatments", "enzyme_activities"):
            for j, item in enumerate(rec.get(section, [])):
                q = re.sub(r"\s+", " ", (item.get("evidence") or {}).get("quote", "")).strip()
                if q and q[:80] not in src:
                    n_missing += 1
                    print(rp.name, "warn", f"{section}/{j}/evidence/quote", "quote not found verbatim in source:", q[:60])
        print(rp.name, "summary:", len(rec.get("phenotypes", [])), "phenotypes,", len(rec.get("measurements", [])), "measurements,",
              len(rec.get("genetic_findings", [])), "variants,", len(rec.get("diagnoses", [])), "diagnoses;", n_missing, "quotes not verbatim")
print("OK" if ok else "ISSUES")
sys.exit(0 if ok else 1)
