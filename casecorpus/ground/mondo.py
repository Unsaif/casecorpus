"""Disease grounding to MONDO (with OMIM / Orphanet cross-references) from mondo.json.
A compact index is cached next to the ontology because mondo.json is >100 MB."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

MONDO_PREFIX = "http://purl.obolibrary.org/obo/MONDO_"
NORM_VERSION = "2"  # bump when _norm/_spell change so the cached index is rebuilt

_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}


def _spell(s: str) -> str:
    """British/American and roman-numeral normalisation shared by all lexical grounders."""
    s = s.lower()
    s = re.sub(r"(?<=[a-z])ae(?=[a-z])", "e", s)      # anaemia -> anemia, haem -> hem
    s = re.sub(r"(?<=[a-z])oe(?=[a-z])", "e", s)      # oedema -> edema, foetal -> fetal
    s = re.sub(r"\btype\s+([ivx]+)\b", lambda m: "type " + _ROMAN.get(m.group(1), m.group(1)), s)
    s = re.sub(r"\b(aciduria|acidaemia)\b", "acidemia", s)
    s = re.sub(r"\bdeficiency of (.+)$", r"\1 deficiency", s)
    return s


def _norm(s: str) -> str:
    s = _spell(s.strip())
    s = re.sub(r"[‐-―]", "-", s)
    s = re.sub(r"\b(type|deficiency of)\b", lambda m: m.group(0), s)
    s = re.sub(r"[^a-z0-9 \-/,]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class MondoGrounder:
    def __init__(self, mondo_json: Path, cache: Path | None = None):
        idx = None
        if cache and cache.exists() and cache.stat().st_mtime >= mondo_json.stat().st_mtime:
            idx = json.load(open(cache))
            if idx.get("norm_version") != NORM_VERSION:
                idx = None
        if idx is None:
            idx = self._build(mondo_json)
            if cache:
                cache.write_text(json.dumps(idx))
        self.label: dict[str, str] = idx["label"]
        self.xref: dict[str, dict[str, list[str]]] = idx["xref"]     # mondo -> {omim:[], orphanet:[]}
        self.by_xref: dict[str, str] = idx["by_xref"]                # OMIM:xxxx / ORPHA:xxxx -> mondo
        self.exact: dict[str, str] = idx["exact"]
        # curated shorthand aliases (data/disease_aliases.tsv) -> OMIM -> MONDO; exact match only
        self.alias: dict[str, str] = {}
        alias_file = Path(__file__).resolve().parent.parent / "data" / "disease_aliases.tsv"
        if alias_file.exists():
            for line in alias_file.read_text().splitlines()[1:]:
                if "\t" in line:
                    a, om = line.split("\t")[:2]
                    mid = self.by_xref.get(om.strip())
                    if mid:
                        self.alias[_norm(a)] = mid
        self._choices = list(self.exact.keys())

    @staticmethod
    def _build(mondo_json: Path) -> dict[str, Any]:
        g = json.load(open(mondo_json))["graphs"][0]
        label, xref, by_xref, exact = {}, {}, {}, {}
        for n in g["nodes"]:
            if n.get("type") != "CLASS" or not n["id"].startswith(MONDO_PREFIX):
                continue
            mid = "MONDO:" + n["id"][len(MONDO_PREFIX):]
            lbl = n.get("lbl", "")
            meta = n.get("meta", {})
            if meta.get("deprecated") or lbl.lower().startswith("obsolete"):
                continue
            label[mid] = lbl
            om = sorted({x["val"] for x in meta.get("xrefs", []) if x["val"].startswith("OMIM:")})
            orp = sorted({x["val"].replace("Orphanet:", "ORPHA:") for x in meta.get("xrefs", []) if x["val"].startswith("Orphanet:")})
            xref[mid] = {"omim": om, "orphanet": orp}
            for x in om + orp:
                by_xref.setdefault(x, mid)
            exact.setdefault(_norm(lbl), mid)
            for s in meta.get("synonyms", []):
                if s.get("pred") in ("hasExactSynonym", "hasRelatedSynonym"):
                    exact.setdefault(_norm(s["val"]), mid)
        return {"norm_version": NORM_VERSION, "label": label, "xref": xref, "by_xref": by_xref, "exact": exact}

    def _pack(self, mid: str) -> dict[str, Any]:
        x = self.xref.get(mid, {})
        return {"label": self.label[mid], "mondo": mid, "omim": (x.get("omim") or [None])[0], "orphanet": (x.get("orphanet") or [None])[0]}

    def ground(self, text: str, supplied: dict[str, Any] | None = None) -> dict[str, Any]:
        supplied = supplied or {}
        # 1. supplied cross-references (OMIM / Orphanet / MONDO) that resolve
        for key in ("mondo", "omim", "orphanet"):
            v = supplied.get(key)
            if not v:
                continue
            mid = v if key == "mondo" else self.by_xref.get(v)
            if mid and mid in self.label:
                sim = fuzz.token_set_ratio(_norm(text), _norm(self.label[mid]))
                if sim >= 60:
                    return {"disease": self._pack(mid), "grounding": {"status": "grounded", "confidence": 0.9, "method": f"supplied {key}", "candidates": []}}
        # 2. OMIM/ORPHA numbers embedded in the text ("(OMIM 251000)")
        m = re.search(r"(?:OMIM|MIM)\s*[#*:]?\s*(\d{6})", text)
        if m and self.by_xref.get(f"OMIM:{m.group(1)}"):
            mid = self.by_xref[f"OMIM:{m.group(1)}"]
            return {"disease": self._pack(mid), "grounding": {"status": "grounded", "confidence": 0.9, "method": "omim-in-text", "candidates": []}}
        key = _norm(re.sub(r"\((?:OMIM|MIM|ORPHA)[^)]*\)", "", text))
        # 3. curated alias, then exact
        if key in self.alias:
            mid = self.alias[key]
            return {"disease": self._pack(mid), "grounding": {"status": "grounded", "confidence": 0.95, "method": "alias", "candidates": []}}
        if key in self.exact:
            mid = self.exact[key]
            return {"disease": self._pack(mid), "grounding": {"status": "grounded", "confidence": 0.95, "method": "exact", "candidates": []}}
        # 4. fuzzy
        cands = []
        if key:
            for choice, score, _ in process.extract(key, self._choices, scorer=fuzz.token_set_ratio, limit=5, score_cutoff=75):
                mid = self.exact[choice]
                cands.append({"id": mid, "label": self.label[mid], "score": round(score / 100, 3)})
        if cands:
            # auto-accept only when the strict (order-sensitive, no subset credit) similarity is also high
            best_key = next(k for k in self._choices if self.exact[k] == cands[0]["id"] and fuzz.token_set_ratio(key, k) >= cands[0]["score"] * 100 - 1)
            strict = fuzz.token_sort_ratio(key, best_key) / 100
            if strict >= 0.97 and (len(cands) == 1 or cands[0]["score"] - cands[1]["score"] >= 0.03 or cands[1]["id"] == cands[0]["id"]):
                return {"disease": self._pack(cands[0]["id"]), "grounding": {"status": "grounded", "confidence": round(strict * 0.9, 3), "method": "fuzzy", "candidates": cands}}
        return {"disease": {"label": supplied.get("label") or text, "mondo": None, "omim": supplied.get("omim"), "orphanet": supplied.get("orphanet")},
                "grounding": {"status": "candidate" if cands else "unresolved", "confidence": None, "method": "fuzzy", "candidates": cands}}
