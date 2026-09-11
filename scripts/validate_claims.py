"""Validate work/<pmid>/claims.json against reaction_claim.schema.json; verify quotes and anchors.
Usage: python validate_claims.py <work_dir>/<pmid>"""
import json, sys
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from casecorpus.anchors import index_markdown, verify_quote  # noqa: E402

SCHEMA = json.load(open(Path(__file__).resolve().parent.parent / "casecorpus" / "schema" / "reaction_claim.schema.json"))
wd = Path(sys.argv[1])
p = wd / "claims.json"
if not p.exists():
    print("MISSING claims.json"); sys.exit(1)
doc = json.load(open(p))
clean = {k: v for k, v in doc.items() if not k.startswith("_")}
ok = True
for e in jsonschema.Draft202012Validator(SCHEMA).iter_errors(clean):
    ok = False
    print("SCHEMA error", "/".join(str(x) for x in e.path), e.message[:250])
md = (wd / "input.md").read_text(); idx = index_markdown(md)
n_missing = n_wrong = 0
for j, c in enumerate(doc.get("claims", [])):
    ev = c.get("evidence") or {}
    v = verify_quote(ev.get("quote", ""), ev.get("anchor", ""), idx, md)
    if not v["in_document"]:
        n_missing += 1; print(f"claims/{j} warn quote not found verbatim:", ev.get("quote", "")[:70])
    elif not v["in_anchor"]:
        n_wrong += 1; print(f"claims/{j} info quote found but not in anchor {ev.get('anchor')!r}")
    for k, q in enumerate(c.get("quantities", [])):
        qv = verify_quote((q.get("evidence") or {}).get("quote", ""), (q.get("evidence") or {}).get("anchor", ""), idx, md)
        if not qv["in_document"]:
            n_missing += 1; print(f"claims/{j}/quantities/{k} warn quote not found verbatim:", (q.get("evidence") or {}).get("quote", "")[:70])
bad = [a for a in doc.get("narrative_anchors", []) if not idx.expand(a)]
for a in bad:
    print("warn narrative anchor does not resolve:", repr(a))
types = {}
for c in doc.get("claims", []):
    types[c.get("claim_type")] = types.get(c.get("claim_type"), 0) + 1
genes = sorted({(c.get("enzyme") or {}).get("gene_symbol") for c in doc.get("claims", []) if (c.get("enzyme") or {}).get("gene_symbol")})
print("summary:", len(doc.get("claims", [])), "claims", types, "| genes", genes, "|", n_missing, "quotes not verbatim;", n_wrong, "outside anchor;", len(doc.get("narrative_anchors", [])), "narrative anchors")
print("OK" if ok else "ISSUES")
sys.exit(0 if ok else 1)
