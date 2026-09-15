"""Full-text retrieval in tiers. Each attempt is recorded; the best tier reached is stored on the document.

Tier 1  epmc        Europe PMC full-text XML (JATS) + supplementary files, for anything inEPMC.
Tier 1b pmc         NCBI PMC efetch (db=pmc) JATS XML — open-access articles reach PMC weeks before Europe PMC.
Tier 2  unpaywall   legal OA copy located by DOI (PDF or landing page), when not in Europe PMC.
Tier 3  publisher   Elsevier / Wiley / Springer Nature TDM APIs under institutional tokens (optional).
Tier 4  abstract    nothing beyond PubMed metadata; the record is still extracted from the abstract.

Full text is stored under raw/<pmid>/ and never redistributed; only extracted facts and short quotes are.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import requests
from tqdm import tqdm

from .config import Settings
from .db import Catalogue
from .harvest import EPMC, RateLimiter

UNPAYWALL = "https://api.unpaywall.org/v2"
PMC_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
ELSEVIER = "https://api.elsevier.com/content/article/doi"
WILEY = "https://api.wiley.com/onlinelibrary/tdm/v1/articles"
SPRINGER_OA = "https://api.springernature.com/openaccess/jats"

UA = "casecorpus/0.1"


class FullTextFetcher:
    def __init__(self, settings: Settings, cat: Catalogue):
        self.s = settings
        self.cat = cat
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"{UA} (mailto:{settings.email or 'unknown'})"
        self.rl = RateLimiter(4.0)

    # ---- tier 1: Europe PMC ------------------------------------------------
    def epmc(self, pmid: str, pmcid: str | None, want_suppl: bool = True) -> tuple[bool, str | None, str]:
        if not pmcid:
            return False, None, "no PMCID"
        d = self.s.raw_dir / pmid
        d.mkdir(parents=True, exist_ok=True)
        self.rl.wait()
        r = self.session.get(f"{EPMC}/{pmcid}/fullTextXML", timeout=120)
        if r.status_code != 200 or not r.content.strip().startswith(b"<"):
            return False, None, f"fullTextXML HTTP {r.status_code}"
        p = d / "epmc.xml"
        p.write_bytes(r.content)
        if want_suppl:
            self.rl.wait()
            rs = self.session.get(f"{EPMC}/{pmcid}/supplementaryFiles", timeout=180)
            if rs.status_code == 200 and rs.content[:2] == b"PK":
                (d / "supplementary.zip").write_bytes(rs.content)
        return True, str(p), "ok"

    # ---- tier 1b: NCBI PMC efetch -------------------------------------------
    def pmc(self, pmid: str, pmcid: str | None) -> tuple[bool, str | None, str]:
        if not pmcid:
            return False, None, "no PMCID"
        self.rl.wait()
        params = {"db": "pmc", "id": pmcid, "retmode": "xml", "tool": "casecorpus"}
        if self.s.email:
            params["email"] = self.s.email
        if self.s.ncbi_api_key:
            params["api_key"] = self.s.ncbi_api_key
        r = self.session.get(PMC_EFETCH, params=params, timeout=180)
        if r.status_code != 200 or b"<body" not in r.content:
            return False, None, f"pmc efetch HTTP {r.status_code}" + ("" if r.status_code != 200 else " (no body: not open access)")
        d = self.s.raw_dir / pmid
        d.mkdir(parents=True, exist_ok=True)
        p = d / "epmc.xml"  # same JATS layout; downstream treats it identically
        p.write_bytes(r.content)
        return True, str(p), "ok (pmc efetch)"

    # ---- tier 2: Unpaywall ---------------------------------------------------
    def unpaywall(self, pmid: str, doi: str | None) -> tuple[bool, str | None, str]:
        if not doi:
            return False, None, "no DOI"
        if not self.s.unpaywall_email:
            return False, None, "UNPAYWALL_EMAIL not set"
        self.rl.wait()
        r = self.session.get(f"{UNPAYWALL}/{doi}", params={"email": self.s.unpaywall_email}, timeout=60)
        if r.status_code != 200:
            return False, None, f"unpaywall HTTP {r.status_code}"
        j = r.json()
        loc = j.get("best_oa_location") or {}
        url = loc.get("url_for_pdf") or loc.get("url")
        if not url:
            return False, None, "no OA location"
        self.rl.wait()
        try:
            rr = self.session.get(url, timeout=120, allow_redirects=True)
        except requests.RequestException as e:
            return False, None, f"download failed: {e}"
        if rr.status_code != 200:
            return False, None, f"download HTTP {rr.status_code}"
        d = self.s.raw_dir / pmid
        d.mkdir(parents=True, exist_ok=True)
        ctype = rr.headers.get("Content-Type", "")
        if rr.content[:4] == b"%PDF" or "pdf" in ctype:
            p = d / "unpaywall.pdf"
        elif "xml" in ctype:
            p = d / "unpaywall.xml"
        else:
            p = d / "unpaywall.html"
        p.write_bytes(rr.content)
        (d / "unpaywall.json").write_text(r.text)
        return True, str(p), f"ok ({loc.get('host_type')}, {loc.get('license')})"

    # ---- tier 3: publisher TDM -----------------------------------------------
    def publisher(self, pmid: str, doi: str | None, issn: str | None) -> tuple[bool, str | None, str]:
        """Best-effort publisher TDM. Which API to try is chosen from the DOI prefix.
        10.1016 Elsevier · 10.1002 / 10.1111 Wiley · 10.1007 / 10.1038 / 10.1186 Springer Nature (BMC is OA anyway)."""
        if not doi:
            return False, None, "no DOI"
        d = self.s.raw_dir / pmid
        prefix = doi.split("/")[0]
        try:
            if prefix == "10.1016" and self.s.elsevier_api_key:
                self.rl.wait()
                r = self.session.get(f"{ELSEVIER}/{doi}", params={"httpAccept": "text/xml"},
                                     headers={"X-ELS-APIKey": self.s.elsevier_api_key,
                                              **({"X-ELS-Insttoken": self.s.elsevier_insttoken} if self.s.elsevier_insttoken else {})},
                                     timeout=120)
                if r.status_code == 200:
                    d.mkdir(parents=True, exist_ok=True)
                    p = d / "elsevier.xml"
                    p.write_bytes(r.content)
                    return True, str(p), "ok (elsevier)"
                return False, None, f"elsevier HTTP {r.status_code}"
            if prefix in ("10.1002", "10.1111") and self.s.wiley_tdm_token:
                self.rl.wait()
                r = self.session.get(f"{WILEY}/{doi}", headers={"Wiley-TDM-Client-Token": self.s.wiley_tdm_token},
                                     timeout=120, allow_redirects=True)
                if r.status_code == 200 and r.content[:4] == b"%PDF":
                    d.mkdir(parents=True, exist_ok=True)
                    p = d / "wiley.pdf"
                    p.write_bytes(r.content)
                    return True, str(p), "ok (wiley)"
                return False, None, f"wiley HTTP {r.status_code}"
            if prefix in ("10.1007", "10.1038", "10.1186", "10.1140") and self.s.springer_api_key:
                self.rl.wait()
                r = self.session.get(SPRINGER_OA, params={"q": f"doi:{doi}", "api_key": self.s.springer_api_key}, timeout=120)
                if r.status_code == 200 and b"<article" in r.content:
                    d.mkdir(parents=True, exist_ok=True)
                    p = d / "springer.xml"
                    p.write_bytes(r.content)
                    return True, str(p), "ok (springer)"
                return False, None, f"springer HTTP {r.status_code}"
        except requests.RequestException as e:
            return False, None, f"error: {e}"
        return False, None, f"no TDM credentials for prefix {prefix}"


def fetch_all(settings: Settings, cat: Catalogue, tiers: tuple[str, ...] = ("epmc", "pmc", "unpaywall", "publisher"),
              limit: int | None = None, retry_failed: bool = False, only_set: str | None = None) -> dict[str, Any]:
    """Fetch full text for documents that have none yet; only_set restricts to a named document set."""
    settings.ensure()
    f = FullTextFetcher(settings, cat)
    where = "(fulltext_tier IS NULL OR fulltext_tier='abstract')"
    params: tuple = ()
    if only_set:
        where += " AND pmid IN (SELECT pmid FROM document_sets WHERE set_name=?)"
        params = (only_set,)
    docs = list(cat.iter_documents(where, params))
    if limit:
        docs = docs[:limit]
    stats: dict[str, int] = {t: 0 for t in tiers}
    stats["abstract_only"] = 0
    for d in tqdm(docs, desc="fulltext"):
        pmid = d["pmid"]
        got = False
        for tier in tiers:
            if not retry_failed and cat.retrieval_attempted(pmid, tier):
                continue
            if tier == "epmc":
                ok, path, status = f.epmc(pmid, d.get("pmcid"))
            elif tier == "pmc":
                ok, path, status = f.pmc(pmid, d.get("pmcid"))
            elif tier == "unpaywall":
                ok, path, status = f.unpaywall(pmid, d.get("doi"))
            elif tier == "publisher":
                ok, path, status = f.publisher(pmid, d.get("doi"), d.get("issn"))
            else:
                continue
            cat.record_retrieval(pmid, tier, ok, path, status)
            if ok:
                stats[tier] += 1
                got = True
                break
        if not got:
            cat.upsert_document({"pmid": pmid, "fulltext_tier": "abstract"})
            stats["abstract_only"] += 1
    return stats
