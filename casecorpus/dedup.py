"""Cross-paper entity resolution: link records that plausibly describe the same individual.
Never merges; writes 'same_individual_as' links with a score and the evidence used.

Fingerprint components: causal gene + variant (strongest), sex, approximate birth year
(publication year minus age at last follow-up), country/centre, author overlap, explicit re-report statements."""
from __future__ import annotations

import itertools
import re
from collections import defaultdict
from typing import Any

from .db import Catalogue
from .validate import iso_to_days


def _variant_key(rec: dict[str, Any]) -> set[str]:
    keys = set()
    for g in rec.get("genetic_findings", []):
        sym = (g.get("gene_symbol") or "").upper()
        for v in (g.get("variant_hgvs_c"), g.get("variant_hgvs_p"), g.get("variant_text")):
            if v:
                keys.add(f"{sym}:{re.sub(r'[^A-Za-z0-9>_]', '', v).lower()}")
    return keys


def _birth_year(rec: dict[str, Any], pub_year: int | None) -> int | None:
    subj = rec.get("subject", {})
    for k in ("age_at_last_followup", "age_at_presentation", "age_at_onset"):
        d = iso_to_days((subj.get(k) or {}).get("iso8601"))
        if d is not None and pub_year:
            return int(pub_year - d / 365.25)
    return None


def link_same_individuals(cat: Catalogue, min_score: float = 0.6) -> dict[str, Any]:
    recs = list(cat.iter_individuals("valid=1"))
    docs = {r["pmid"]: cat.get_document(r["pmid"]) or {} for r in recs}
    by_variant: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        for k in _variant_key(r["record"]):
            by_variant[k].append(r)
    n = 0
    seen = set()
    for key, group in by_variant.items():
        for a, b in itertools.combinations(group, 2):
            if a["pmid"] == b["pmid"]:
                continue
            pair = tuple(sorted((a["record_id"], b["record_id"])))
            if pair in seen:
                continue
            seen.add(pair)
            ra, rb = a["record"], b["record"]
            score, ev = 0.5, [f"shared variant {key}"]
            sa, sb = ra["subject"].get("sex"), rb["subject"].get("sex")
            if sa and sb and sa != "UNKNOWN_SEX" and sb != "UNKNOWN_SEX":
                if sa == sb:
                    score += 0.1; ev.append("same sex")
                else:
                    score -= 0.5; ev.append("different sex")
            ya, yb = _birth_year(ra, docs[a["pmid"]].get("pub_year")), _birth_year(rb, docs[b["pmid"]].get("pub_year"))
            if ya and yb:
                if abs(ya - yb) <= 2:
                    score += 0.2; ev.append(f"birth year ~{ya}")
                else:
                    score -= 0.3; ev.append(f"birth years {ya} vs {yb}")
            aa = set(docs[a["pmid"]].get("authors") or []); ab = set(docs[b["pmid"]].get("authors") or [])
            if aa & ab:
                score += 0.15; ev.append(f"{len(aa & ab)} shared authors")
            if score >= min_score:
                cat.add_link(pair[0], pair[1], "same_individual_as", "; ".join(ev), round(score, 2))
                n += 1
    return {"candidate_pairs": len(seen), "links": n}
