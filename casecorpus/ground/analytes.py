"""Analyte grounding: built-in IEM lexicon (data/analytes_builtin.tsv) + optional ChEBI names
(ontologies/chebi_names.tsv.gz from https://ftp.ebi.ac.uk/pub/databases/chebi/Flat_file_tab_delimited/names.tsv.gz)
+ optional VMH metabolite table (ontologies/vmh_metabolites.tsv with columns abbreviation, fullName, chebiId[, hmdb]).

Specimen words and panel words are stripped before matching ("plasma ammonia" -> "ammonia")."""
from __future__ import annotations

import csv
import gzip
import re
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

_SPECIMEN_WORDS = r"\b(plasma|serum|blood|urinary|urine|csf|cerebrospinal fluid|dried blood spot|dbs|whole blood|fibroblast|leukocyte|level|levels|concentration|concentrations|of)\b"

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
    s = re.sub(_SPECIMEN_WORDS, " ", s)
    s = re.sub(r"[^a-z0-9 \-/:,\.]", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -")


class AnalyteGrounder:
    def __init__(self, ontologies: Path):
        self.entries: dict[str, dict[str, Any]] = {}   # label -> {chebi, vmh, kind}
        self.exact: dict[str, str] = {}                 # normalised name/synonym -> label
        builtin = Path(__file__).resolve().parent.parent / "data" / "analytes_builtin.tsv"
        with open(builtin, newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                self.entries[row["label"]] = {"chebi": row["chebi"] or None, "vmh": row["vmh"] or None, "kind": row["kind"], "source": "builtin"}
                self.exact.setdefault(_norm(row["label"]), row["label"])
                for s in (row["synonyms"] or "").split("|"):
                    if s:
                        self.exact.setdefault(_norm(s), row["label"])
        vmh = ontologies / "vmh_metabolites.tsv"
        if vmh.exists():
            with open(vmh, newline="") as fh:
                for row in csv.DictReader(fh, delimiter="\t"):
                    label = row.get("fullName") or row.get("name")
                    if not label:
                        continue
                    chebi = row.get("chebiId") or row.get("chebi")
                    chebi = f"CHEBI:{chebi}" if chebi and not str(chebi).startswith("CHEBI:") else chebi
                    e = self.entries.setdefault(label, {"chebi": None, "vmh": None, "kind": "metabolite", "source": "vmh"})
                    e["vmh"] = e["vmh"] or row.get("abbreviation")
                    e["chebi"] = e["chebi"] or chebi
                    self.exact.setdefault(_norm(label), label)
        chebi_names = ontologies / "chebi_names.tsv.gz"
        if chebi_names.exists():
            with gzip.open(chebi_names, "rt") as fh:
                for row in csv.DictReader(fh, delimiter="\t"):
                    name = row.get("NAME")
                    cid = row.get("COMPOUND_ID")
                    if not name or not cid or len(name) < 3:
                        continue
                    key = _norm(name)
                    if key not in self.exact:
                        self.entries.setdefault(name, {"chebi": f"CHEBI:{cid}", "vmh": None, "kind": "metabolite", "source": "chebi"})
                        self.exact[key] = name
        self._choices = list(self.exact.keys())

    def ground(self, text: str, supplied_chebi: str | None = None) -> dict[str, Any]:
        # try the whole string, then the string without parentheticals, then each parenthetical / trailing code alone
        variants = [text, re.sub(r"\(.*?\)", " ", text)] + re.findall(r"\(([^)]+)\)", text) + re.findall(r"\b(C\d{1,2}(?::\d)?(?:-?(?:OH|DC))?)\b", text)
        for v in variants:
            k = _norm(v)
            if k in self.exact:
                label = self.exact[k]
                e = self.entries[label]
                return {"ids": {"label": label, "chebi": e["chebi"] or supplied_chebi, "vmh": e["vmh"]},
                        "grounding": {"status": "grounded", "confidence": 0.95 if v == text else 0.9, "method": f"exact:{e['source']}", "candidates": []}}
        key = _norm(re.sub(r"\(.*?\)", " ", text))
        if key in self.exact:
            label = self.exact[key]
            e = self.entries[label]
            return {"ids": {"label": label, "chebi": e["chebi"] or supplied_chebi, "vmh": e["vmh"]},
                    "grounding": {"status": "grounded", "confidence": 0.95, "method": f"exact:{e['source']}", "candidates": []}}
        cands = []
        if key:
            for choice, score, _ in process.extract(key, self._choices, scorer=fuzz.WRatio, limit=5, score_cutoff=80):
                label = self.exact[choice]
                cands.append({"id": self.entries[label]["chebi"] or label, "label": label, "score": round(score / 100, 3)})
        if cands and cands[0]["score"] >= 0.95 and (len(cands) == 1 or cands[0]["label"] == cands[1]["label"] or cands[0]["score"] - cands[1]["score"] >= 0.05):
            label = cands[0]["label"]
            e = self.entries[label]
            return {"ids": {"label": label, "chebi": e["chebi"] or supplied_chebi, "vmh": e["vmh"]},
                    "grounding": {"status": "grounded", "confidence": round(cands[0]["score"] * 0.9, 3), "method": "fuzzy", "candidates": cands}}
        return {"ids": {"label": None, "chebi": supplied_chebi, "vmh": None},
                "grounding": {"status": "candidate" if cands else "unresolved", "confidence": None, "method": "fuzzy", "candidates": cands}}
