"""Pilot-2 machinery: per-disease harvest sets and a stratified document sample for human-verified evaluation.

Disease sets come from a TSV (default: data/pilot2_diseases.tsv) with columns
  key, label, mondo, genes, mesh, tiab (|-separated), gene_tiab (|-separated, only unambiguous symbols), why
Each disease becomes a PubMed query  (MeSH OR tiab terms OR gene symbols) AND Case Reports[pt]  and a
document set "<prefix>:<key>" in the catalogue. `draw_sample` then picks n documents per set, stratified by
full-text availability x year band x document kind, deterministically (seeded), and registers the result as
another document set so `fulltext --only-set`, `prepare --set` and `review-export` can address it.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import Settings
from .db import Catalogue

DEFAULT_DISEASES = Path(__file__).resolve().parent / "data" / "pilot2_diseases.tsv"
FULLTEXT_TIERS = ("epmc", "pmc", "unpaywall", "publisher")


@dataclass
class DiseaseSet:
    key: str
    label: str
    mondo: str
    genes: list[str]
    mesh: str
    tiab: list[str]
    gene_tiab: list[str] = field(default_factory=list)
    why: str = ""

    def pubmed_query(self, case_reports_only: bool = True) -> str:
        parts: list[str] = []
        if self.mesh:
            parts.append(self.mesh if self.mesh.endswith("]") else f'"{self.mesh}"[MeSH Terms]')
        parts += [f'"{t}"[tiab]' for t in self.tiab]
        parts += [f'"{g}"[tiab]' for g in self.gene_tiab]
        q = "(" + " OR ".join(parts) + ")"
        return f'{q} AND ("Case Reports"[pt])' if case_reports_only else q


def load_disease_sets(path: Path | None = None) -> list[DiseaseSet]:
    p = path or DEFAULT_DISEASES
    out = []
    with open(p, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if not row.get("key"):
                continue
            out.append(DiseaseSet(
                key=row["key"].strip(), label=row["label"].strip(), mondo=row.get("mondo", "").strip(),
                genes=[g for g in (row.get("genes") or "").split("|") if g],
                mesh=(row.get("mesh") or "").strip(),
                tiab=[t.strip() for t in (row.get("tiab") or "").split("|") if t.strip()],
                gene_tiab=[g.strip() for g in (row.get("gene_tiab") or "").split("|") if g.strip()],
                why=(row.get("why") or "").strip(),
            ))
    return out


# ---------------------------------------------------------------------------------------------- harvest
def harvest_disease_sets(settings: Settings, cat: Catalogue, sets: Iterable[DiseaseSet], prefix: str = "pilot2",
                         limit_per_set: int | None = None, mindate: str | None = None) -> dict[str, Any]:
    """One PubMed harvest per disease; documents land in the catalogue (layer A_seed) and in set <prefix>:<key>."""
    from .harvest import harvest_seed
    out: dict[str, Any] = {}
    for ds in sets:
        name = f"{prefix}:{ds.key}"
        res = harvest_seed(settings, cat, query=ds.pubmed_query(), layer="A_seed", mindate=mindate, limit=limit_per_set, set_name=name)
        out[name] = {"count": res["count"], "upserted": res["upserted"]}
    return out


def count_disease_sets(settings: Settings, sets: Iterable[DiseaseSet]) -> list[dict[str, Any]]:
    """PubMed hit counts per disease query (and per clause) — sanity check before harvesting."""
    from .harvest import PubMed
    pm = PubMed(settings)
    rows = []
    for ds in sets:
        row = {"key": ds.key, "label": ds.label, "query": ds.pubmed_query(), "case_reports": pm.count(ds.pubmed_query()),
               "all_pubtypes": pm.count(ds.pubmed_query(case_reports_only=False))}
        if ds.mesh:
            m = ds.mesh if ds.mesh.endswith("]") else f'"{ds.mesh}"[MeSH Terms]'
            row["mesh_only"] = pm.count(f'{m} AND ("Case Reports"[pt])')
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------------------------- strata
_NUM = r"(?:\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty)"
_SERIES = re.compile(rf"\b{_NUM}\s+(?:unrelated\s+|new\s+|additional\s+|further\s+|japanese\s+|chinese\s+|turkish\s+|italian\s+|\w+\s+)?(?:patients|cases|children|infants|siblings|sibs|families|individuals|probands|subjects|brothers|sisters|neonates|newborns|adults|women|men|boys|girls)\b", re.I)
_SERIES_WORDS = re.compile(r"\b(case series|series of|cohort|retrospective|prospective|registry|multicenter|multicentre|natural history|long-term follow-up of \d+|clinical spectrum|in \d+ patients)\b", re.I)
_TWO_CASES = re.compile(r"\b(two|three|2|3) (cases|patients|siblings|sibs|brothers|sisters|families)\b", re.I)
_REVIEW = re.compile(r"\b(review of the literature|literature review|systematic review|review of \d+ (reported )?cases|reported cases)\b", re.I)


def document_kind(doc: dict[str, Any]) -> str:
    """Coarse document kind from publication types + title/abstract wording:
    single_case | small_series (2-3) | series (>=4 or cohort wording) | case_plus_review | other."""
    pt = set(doc.get("pub_types") or [])
    title = doc.get("title") or ""
    abstract = doc.get("abstract") or ""
    text = f"{title}\n{abstract[:600]}"
    is_review = bool(_REVIEW.search(text)) or "Review" in pt or "Systematic Review" in pt
    if _SERIES_WORDS.search(text) or _SERIES.search(title) or "Multicenter Study" in pt or "Observational Study" in pt or "Clinical Study" in pt:
        m = _SERIES.search(title) or _SERIES.search(text)
        if m and _TWO_CASES.match(m.group(0)):
            return "small_series"
        return "series"
    if _TWO_CASES.search(title) or _TWO_CASES.search(abstract[:300]):
        return "small_series"
    if "Case Reports" in pt:
        return "case_plus_review" if is_review else "single_case"
    return "other" if not _SERIES.search(text) else "series"


def year_band(year: int | None) -> str:
    if not year:
        return "unknown"
    if year <= 1999:
        return "pre2000"
    if year <= 2014:
        return "2000-2014"
    return "2015+"


def fulltext_expected(doc: dict[str, Any]) -> bool:
    """Full text available or likely available (before `fulltext` has run: in Europe PMC / OA / has a PMCID)."""
    if doc.get("fulltext_tier") in FULLTEXT_TIERS:
        return True
    if doc.get("fulltext_tier") == "abstract":
        return False
    return bool(doc.get("in_epmc") or doc.get("is_open_access") or doc.get("pmcid"))


def _rank(pmid: str, seed: int) -> str:
    return hashlib.sha1(f"{seed}:{pmid}".encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- sampling
def draw_sample(cat: Catalogue, set_names: list[str], per_set: int = 25, seed: int = 1, fulltext_share: float = 0.72,
                min_series: int = 4, min_per_band: int = 2, exclude_sets: Iterable[str] = (),
                languages: tuple[str, ...] | None = ("eng",)) -> list[dict[str, Any]]:
    """Deterministic stratified sample: per set, ~fulltext_share of documents with (expected) full text; within
    each full-text/abstract half, every year band gets >= min_per_band when available and at least min_series
    documents are series/small_series (per set) when available; the rest is filled proportionally to stratum size.
    Documents in several disease sets are assigned to the first set that draws them. Documents in exclude_sets
    (e.g. an earlier pilot) are skipped."""
    excluded = set()
    for s in exclude_sets:
        excluded.update(cat.set_members(s))
    taken: set[str] = set()
    out: list[dict[str, Any]] = []
    for sname in set_names:
        docs = [cat.get_document(p) for p in cat.set_members(sname)]
        docs = [d for d in docs if d and d["pmid"] not in taken and d["pmid"] not in excluded
                and (languages is None or (d.get("language") or "eng") in languages)]
        for d in docs:
            d["_ft"] = fulltext_expected(d)
            d["_band"] = year_band(d.get("pub_year"))
            d["_kind"] = document_kind(d)
            d["_rank"] = _rank(d["pmid"], seed)
        docs.sort(key=lambda d: d["_rank"])
        n_ft_target = round(per_set * fulltext_share)
        ft_docs = [d for d in docs if d["_ft"]]
        ab_docs = [d for d in docs if not d["_ft"]]
        n_ft = min(n_ft_target, len(ft_docs))
        n_ab = min(per_set - n_ft, len(ab_docs))
        if n_ft + n_ab < per_set:  # top up from whichever half has more
            n_ft = min(per_set - n_ab, len(ft_docs))
        chosen = _pick(ft_docs, n_ft, min_series, min_per_band) + _pick(ab_docs, n_ab, max(1, min_series // 3), min_per_band)
        for d in chosen:
            taken.add(d["pmid"])
            out.append({"set": sname, "pmid": d["pmid"], "pub_year": d.get("pub_year"), "year_band": d["_band"],
                        "fulltext_expected": int(d["_ft"]), "fulltext_tier": d.get("fulltext_tier"), "kind": d["_kind"],
                        "pmcid": d.get("pmcid"), "doi": d.get("doi"), "journal": d.get("journal_iso") or d.get("journal"),
                        "title": d.get("title"), "other_sets": [s for s in cat.sets_of(d["pmid"]) if s != sname]})
    return out


def _pick(docs: list[dict[str, Any]], n: int, min_series: int, min_per_band: int) -> list[dict[str, Any]]:
    """Pick n docs (already shuffled by rank): quotas for series kinds and year bands first, then proportional fill."""
    if n <= 0 or not docs:
        return []
    chosen: list[dict[str, Any]] = []
    used: set[str] = set()

    def take(cands: Iterable[dict[str, Any]], k: int) -> None:
        for d in cands:
            if len(chosen) >= n or k <= 0:
                return
            if d["pmid"] in used:
                continue
            chosen.append(d)
            used.add(d["pmid"])
            k -= 1

    take([d for d in docs if d["_kind"] in ("series", "small_series")], min_series)
    bands = sorted({d["_band"] for d in docs})
    for b in bands:
        have = sum(1 for d in chosen if d["_band"] == b)
        take([d for d in docs if d["_band"] == b], max(0, min_per_band - have))
    # proportional fill by (band) — walk the shuffled list, keeping bands roughly proportional to availability
    remaining = n - len(chosen)
    if remaining > 0:
        totals = {b: sum(1 for d in docs if d["_band"] == b) for b in bands}
        tot = sum(totals.values()) or 1
        quota = {b: max(0, round(remaining * totals[b] / tot)) for b in bands}
        for b in bands:
            take([d for d in docs if d["_band"] == b], quota[b])
        take(docs, n - len(chosen))  # rounding leftovers
    return chosen


def write_sample(rows: list[dict[str, Any]], out_tsv: Path) -> None:
    out_tsv.parent.mkdir(parents=True, exist_ok=True)
    cols = ["set", "pmid", "pub_year", "year_band", "fulltext_expected", "fulltext_tier", "kind", "pmcid", "doi", "journal", "title", "other_sets"]
    with open(out_tsv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t")
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["other_sets"] = "|".join(r.get("other_sets") or [])
            w.writerow({k: r.get(k) for k in cols})


def sample_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from collections import Counter
    return {
        "n": len(rows),
        "per_set": dict(Counter(r["set"] for r in rows)),
        "fulltext_expected": dict(Counter("yes" if r["fulltext_expected"] else "no" for r in rows)),
        "year_band": dict(Counter(r["year_band"] for r in rows)),
        "kind": dict(Counter(r["kind"] for r in rows)),
    }


def register_sample(cat: Catalogue, rows: list[dict[str, Any]], name: str, replace: bool = True) -> int:
    if replace:
        cat.clear_set(name)
    for r in rows:
        cat.add_to_set(r["pmid"], name, json.dumps({"from": r["set"], "kind": r["kind"], "band": r["year_band"]}))
    return len(rows)
