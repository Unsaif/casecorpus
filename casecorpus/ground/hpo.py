"""HPO grounding from hp.json (obographs). Lexical: exact label/synonym match, then fuzzy candidates."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

HP_PREFIX = "http://purl.obolibrary.org/obo/HP_"

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
    s = re.sub(r"[^a-z0-9 \-/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class HPOGrounder:
    def __init__(self, hp_json: Path):
        g = json.load(open(hp_json))["graphs"][0]
        self.label: dict[str, str] = {}
        self.exact: dict[str, str] = {}          # normalised string -> HP id (labels + exact synonyms)
        self.related: dict[str, str] = {}        # normalised string -> HP id (related/broad/narrow synonyms)
        self.obsolete: set[str] = set()
        for n in g["nodes"]:
            if n.get("type") != "CLASS" or not n["id"].startswith(HP_PREFIX):
                continue
            hid = "HP:" + n["id"][len(HP_PREFIX):]
            lbl = n.get("lbl", "")
            meta = n.get("meta", {})
            if meta.get("deprecated") or lbl.lower().startswith("obsolete"):
                self.obsolete.add(hid)
                continue
            self.label[hid] = lbl
            self.exact.setdefault(_norm(lbl), hid)
            for s in meta.get("synonyms", []):
                key = _norm(s["val"])
                if s.get("pred") == "hasExactSynonym":
                    self.exact.setdefault(key, hid)
                else:
                    self.related.setdefault(key, hid)
        # phenotypic abnormality subtree only would be ideal; keep all non-obsolete for now
        self._choices = list(self.exact.keys()) + [k for k in self.related if k not in self.exact]
        self._choice_id = {**self.related, **self.exact}

    def ground(self, text: str, supplied_id: str | None = None) -> dict[str, Any]:
        key = _norm(text)
        cands: list[dict[str, Any]] = []
        # 1. supplied id validates and label agrees reasonably -> keep
        if supplied_id and supplied_id in self.label:
            sim = fuzz.token_set_ratio(key, _norm(self.label[supplied_id]))
            if key in self.exact and self.exact[key] == supplied_id or sim >= 80:
                return {"id": supplied_id, "label": self.label[supplied_id],
                        "grounding": {"status": "grounded", "confidence": 0.95 if sim >= 90 else 0.85, "method": "supplied+verified", "candidates": []}}
        # 2. exact lexical
        if key in self.exact:
            hid = self.exact[key]
            return {"id": hid, "label": self.label[hid], "grounding": {"status": "grounded", "confidence": 0.95, "method": "exact", "candidates": []}}
        if key in self.related:
            hid = self.related[key]
            return {"id": hid, "label": self.label[hid], "grounding": {"status": "grounded", "confidence": 0.85, "method": "synonym", "candidates": []}}
        # 3. fuzzy
        if key:
            for choice, score, _ in process.extract(key, self._choices, scorer=fuzz.token_set_ratio, limit=5, score_cutoff=70):
                hid = self._choice_id[choice]
                cands.append({"id": hid, "label": self.label[hid], "score": round(score / 100, 3)})
        if cands:
            best_key = next(k for k in self._choices if self._choice_id[k] == cands[0]["id"] and fuzz.token_set_ratio(key, k) >= cands[0]["score"] * 100 - 1)
            strict = fuzz.token_sort_ratio(key, best_key) / 100
            if strict >= 0.93 and (len(cands) == 1 or cands[0]["score"] - cands[1]["score"] >= 0.05 or cands[1]["id"] == cands[0]["id"]):
                return {"id": cands[0]["id"], "label": cands[0]["label"],
                        "grounding": {"status": "grounded", "confidence": round(strict * 0.9, 3), "method": "fuzzy", "candidates": cands}}
        if supplied_id and supplied_id in self.label:
            return {"id": supplied_id, "label": self.label[supplied_id],
                    "grounding": {"status": "candidate", "confidence": 0.5, "method": "supplied-unverified", "candidates": cands}}
        return {"id": None, "label": None, "grounding": {"status": "candidate" if cands else "unresolved", "confidence": None, "method": "fuzzy", "candidates": cands}}
