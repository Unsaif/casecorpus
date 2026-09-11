"""Metadata harvest: PubMed E-utilities (history server) + Europe PMC enrichment.

Layer A (seed):   ("Metabolism, Inborn Errors"[MeSH]) AND ("Case Reports"[pt])   -- configurable
Layer A' (lag):   ("Case Reports"[pt]) AND (<scope terms or genes>[tiab]) NOT <seed>   -- catches
                  papers not yet MeSH-indexed or indexed elsewhere
Layer B (recall): full-text section search on Europe PMC (CASE:) intersected with scope terms.

Every document gets: PubMed metadata, Europe PMC OA/licence flags, scope tags, corpus layer.
All calls are rate-limited and resumable; re-running only adds/updates.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterable, Iterator

import requests
from tqdm import tqdm

from .config import Settings
from .db import Catalogue
from .pubmed_xml import parse_pubmed_xml
from .scope import ScopeMatcher, load_scope

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"

DEFAULT_SEED_QUERY = '("Metabolism, Inborn Errors"[MeSH]) AND ("Case Reports"[pt])'


class RateLimiter:
    def __init__(self, per_second: float):
        self.min_interval = 1.0 / per_second
        self._last = 0.0

    def wait(self) -> None:
        now = time.time()
        delta = now - self._last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last = time.time()


class PubMed:
    def __init__(self, settings: Settings):
        self.s = settings
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"casecorpus/0.1 (mailto:{settings.email or 'unknown'})"
        self.rl = RateLimiter(9.0 if settings.ncbi_api_key else 2.5)

    def _params(self, **kw: Any) -> dict[str, Any]:
        p = {"tool": "casecorpus", **kw}
        if self.s.email:
            p["email"] = self.s.email
        if self.s.ncbi_api_key:
            p["api_key"] = self.s.ncbi_api_key
        return p

    def _get(self, url: str, retries: int = 5, **params: Any) -> requests.Response:
        """POST (E-utilities accept it for every endpoint) so long term lists never hit URL-length limits."""
        for attempt in range(retries):
            self.rl.wait()
            r = self.session.post(url, data=self._params(**params), timeout=180)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
        raise RuntimeError(f"E-utilities failed after {retries} attempts: {url}")

    def count(self, term: str) -> int:
        r = self._get(f"{EUTILS}/esearch.fcgi", db="pubmed", term=term, retmode="json", retmax=0)
        return int(r.json()["esearchresult"]["count"])

    def search_history(self, term: str, mindate: str | None = None, maxdate: str | None = None) -> tuple[str, str, int]:
        params: dict[str, Any] = {"db": "pubmed", "term": term, "usehistory": "y", "retmode": "json", "retmax": 0}
        if mindate or maxdate:
            params.update({"datetype": "edat", "mindate": mindate or "1900/01/01", "maxdate": maxdate or "3000/01/01"})
        r = self._get(f"{EUTILS}/esearch.fcgi", **params)
        j = r.json()["esearchresult"]
        return j["webenv"], j["querykey"], int(j["count"])

    def fetch_history(self, webenv: str, query_key: str, count: int, batch: int = 500) -> Iterator[bytes]:
        for start in range(0, count, batch):
            r = self._get(f"{EUTILS}/efetch.fcgi", db="pubmed", WebEnv=webenv, query_key=query_key,
                          retstart=start, retmax=batch, retmode="xml")
            yield r.content

    def fetch_pmids(self, pmids: Iterable[str], batch: int = 200) -> Iterator[bytes]:
        ids = list(pmids)
        for i in range(0, len(ids), batch):
            self.rl.wait()
            r = self.session.post(f"{EUTILS}/efetch.fcgi", data=self._params(db="pubmed", id=",".join(ids[i:i + batch]), retmode="xml"), timeout=120)
            r.raise_for_status()
            yield r.content


class EuropePMC:
    """Enrichment (OA flag, licence, inEPMC, hasSuppl, PMCID) and full-text section search."""

    def __init__(self, settings: Settings):
        self.s = settings
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"casecorpus/0.1 (mailto:{settings.email or 'unknown'})"
        self.rl = RateLimiter(5.0)

    def search(self, query: str, page_size: int = 1000, result_type: str = "core", max_pages: int | None = None) -> Iterator[dict[str, Any]]:
        cursor = "*"
        pages = 0
        while True:
            self.rl.wait()
            r = self.session.get(f"{EPMC}/search", params={"query": query, "format": "json", "resultType": result_type,
                                                        "pageSize": page_size, "cursorMark": cursor}, timeout=120)
            r.raise_for_status()
            j = r.json()
            for res in j.get("resultList", {}).get("result", []):
                yield res
            nxt = j.get("nextCursorMark")
            pages += 1
            if not nxt or nxt == cursor or (max_pages and pages >= max_pages):
                break
            cursor = nxt

    def enrich_pmids(self, pmids: list[str]) -> dict[str, dict[str, Any]]:
        """Return {pmid: {pmcid, is_open_access, license, in_epmc, has_suppl}} for a batch (<= ~100 ids per query)."""
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(pmids), 100):
            chunk = pmids[i:i + 100]
            q = "SRC:MED AND (" + " OR ".join(f"EXT_ID:{p}" for p in chunk) + ")"
            for res in self.search(q, page_size=100, result_type="core"):
                pmid = res.get("pmid")
                if not pmid:
                    continue
                out[pmid] = {
                    "pmcid": res.get("pmcid"),
                    "is_open_access": 1 if res.get("isOpenAccess") == "Y" else 0,
                    "license": res.get("license"),
                    "in_epmc": 1 if res.get("inEPMC") == "Y" else 0,
                    "has_suppl": 1 if res.get("hasSuppl") == "Y" else 0,
                }
        return out


def _doc_text(d: dict[str, Any]) -> str:
    parts = [d.get("title") or "", d.get("abstract") or "", " ".join(d.get("keywords") or [])]
    parts += [m["descriptor"] for m in (d.get("mesh") or [])]
    return "\n".join(parts)


def harvest_seed(settings: Settings, cat: Catalogue, query: str = DEFAULT_SEED_QUERY, layer: str = "A_seed",
                 mindate: str | None = None, limit: int | None = None) -> dict[str, Any]:
    """Run a PubMed query through the history server and upsert every record."""
    settings.ensure()
    pm = PubMed(settings)
    matcher = ScopeMatcher(load_scope(settings.scope_dir)) if (settings.scope_dir / "scope.json").exists() else None
    webenv, qk, count = pm.search_history(query, mindate=mindate)
    if limit:
        count = min(count, limit)
    n = 0
    with tqdm(total=count, desc=f"harvest {layer}") as bar:
        for xml in pm.fetch_history(webenv, qk, count):
            for d in parse_pubmed_xml(xml):
                d["corpus_layer"] = layer
                if matcher:
                    d["scope_tags"] = matcher.tag(_doc_text(d))
                existing = cat.get_document(d["pmid"])
                if existing and existing.get("corpus_layer") and existing["corpus_layer"] < layer:
                    d["corpus_layer"] = existing["corpus_layer"]  # keep the most precise layer
                cat.upsert_document(d)
                n += 1
                bar.update(1)
                if limit and n >= limit:
                    break
            if limit and n >= limit:
                break
    return {"query": query, "layer": layer, "count": count, "upserted": n}


def _chunks(seq: list[str], n: int) -> Iterator[list[str]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def harvest_lag(settings: Settings, cat: Catalogue, seed_query: str = DEFAULT_SEED_QUERY, years_back: int = 3,
                max_terms_per_query: int = 100, limit: int | None = None) -> dict[str, Any]:
    """Layer A': recent Case Reports mentioning scope disease names or genes in title/abstract but not
    caught by the MeSH seed (indexing lag or missing MeSH)."""
    scope_dir = settings.scope_dir
    terms = [t for t in (scope_dir / "terms.txt").read_text().splitlines() if len(t) >= 8 and not re.search(r"[()\[\]\":*?]", t)]
    genes = [g for g in (scope_dir / "genes.txt").read_text().splitlines() if len(g) >= 3]
    pm = PubMed(settings)
    year = time.gmtime().tm_year
    date_clause = f' AND ("{year - years_back}"[dp] : "3000"[dp])'
    total = 0
    queries: list[str] = []
    for chunk in _chunks(sorted(set(terms)), max_terms_per_query):
        tiab = " OR ".join(f'"{t}"[tiab]' for t in chunk)
        queries.append(f'("Case Reports"[pt]) AND ({tiab}) NOT ({seed_query}){date_clause}')
    for chunk in _chunks(sorted(set(genes)), max_terms_per_query):
        tiab = " OR ".join(f'"{g}"[tiab]' for g in chunk)
        queries.append(f'("Case Reports"[pt]) AND ({tiab}) NOT ({seed_query}){date_clause}')
    for i, q in enumerate(tqdm(queries, desc="harvest A_lag queries")):
        res = harvest_seed(settings, cat, query=q, layer="A_lag", limit=limit)
        total += res["upserted"]
        if limit and total >= limit:
            break
    return {"queries": len(queries), "upserted": total}


def harvest_epmc_case_sections(settings: Settings, cat: Catalogue, max_terms_per_query: int = 40, limit: int | None = None) -> dict[str, Any]:
    """Layer B: Europe PMC full-text search for papers with a case-presentation section that mentions
    scope terms, regardless of PubMed publication type. Only adds PMIDs not already present."""
    scope_dir = settings.scope_dir
    terms = [t for t in (scope_dir / "terms.txt").read_text().splitlines() if len(t) >= 10 and " " in t]
    ep = EuropePMC(settings)
    pm = PubMed(settings)
    new_pmids: set[str] = set()
    for chunk in tqdm(list(_chunks(sorted(set(terms)), max_terms_per_query)), desc="harvest B_recall (EPMC CASE:)"):
        q = "SRC:MED AND HAS_FT:y AND (CASE:patient OR CASE:proband) AND (" + " OR ".join(f'"{t}"' for t in chunk) + ")"
        for res in ep.search(q, page_size=1000, result_type="lite"):
            pmid = res.get("pmid")
            if pmid and not cat.get_document(pmid):
                new_pmids.add(pmid)
        if limit and len(new_pmids) >= limit:
            break
    matcher = ScopeMatcher(load_scope(scope_dir))
    n = 0
    ids = sorted(new_pmids)[: limit or None]
    for xml in pm.fetch_pmids(ids):
        for d in parse_pubmed_xml(xml):
            d["corpus_layer"] = "B_recall"
            d["scope_tags"] = matcher.tag(_doc_text(d))
            cat.upsert_document(d)
            n += 1
    return {"candidates": len(new_pmids), "upserted": n}


def enrich(settings: Settings, cat: Catalogue, only_missing: bool = True, limit: int | None = None) -> dict[str, Any]:
    """Add Europe PMC OA/licence/inEPMC flags to documents."""
    ep = EuropePMC(settings)
    where = "in_epmc IS NULL" if only_missing else "1=1"
    pmids = [d["pmid"] for d in cat.iter_documents(where)]
    if limit:
        pmids = pmids[:limit]
    n = 0
    for chunk in tqdm(list(_chunks(pmids, 100)), desc="enrich (Europe PMC)"):
        info = ep.enrich_pmids(chunk)
        for pmid in chunk:
            row = info.get(pmid, {"in_epmc": 0, "is_open_access": 0})
            row["pmid"] = pmid
            existing = cat.get_document(pmid) or {}
            if existing.get("pmcid") and not row.get("pmcid"):
                row.pop("pmcid", None)
            cat.upsert_document(row)
            n += 1
    return {"enriched": n}


def import_pmids(settings: Settings, cat: Catalogue, pmids: list[str], layer: str = "C_manual") -> dict[str, Any]:
    """Add explicit PMIDs (e.g. Phenopacket Store sources, OMIM references)."""
    pm = PubMed(settings)
    matcher = ScopeMatcher(load_scope(settings.scope_dir)) if (settings.scope_dir / "scope.json").exists() else None
    n = 0
    for xml in pm.fetch_pmids(pmids):
        for d in parse_pubmed_xml(xml):
            d["corpus_layer"] = layer
            if matcher:
                d["scope_tags"] = matcher.tag(_doc_text(d))
            cat.upsert_document(d)
            n += 1
    return {"requested": len(pmids), "upserted": n}
