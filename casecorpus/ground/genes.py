"""Gene symbol validation. Uses HGNC complete set when present (ontologies/hgnc_complete_set.txt,
https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt), otherwise the
symbols in HPO's genes_to_disease.txt (covers rare-disease genes, no aliases)."""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any


class GeneGrounder:
    def __init__(self, ontologies: Path):
        self.symbol_to_hgnc: dict[str, str] = {}
        self.alias_to_symbol: dict[str, str] = {}
        self.source = "none"
        hgnc = ontologies / "hgnc_complete_set.txt"
        g2d = ontologies / "genes_to_disease.txt"
        if hgnc.exists():
            self.source = "hgnc"
            with open(hgnc, newline="") as fh:
                for row in csv.DictReader(fh, delimiter="\t"):
                    sym = row["symbol"]
                    self.symbol_to_hgnc[sym] = row["hgnc_id"]
                    for col in ("alias_symbol", "prev_symbol"):
                        for a in (row.get(col) or "").split("|"):
                            if a and a not in self.symbol_to_hgnc:
                                self.alias_to_symbol.setdefault(a, sym)
        elif g2d.exists():
            self.source = "hpo-genes_to_disease"
            with open(g2d) as fh:
                header = fh.readline().rstrip("\n").split("\t")
                i_sym = header.index("gene_symbol")
                for line in fh:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) > i_sym:
                        self.symbol_to_hgnc.setdefault(parts[i_sym], "")

    def ground(self, symbol: str) -> dict[str, Any]:
        s = (symbol or "").strip()
        s_clean = re.sub(r"[^A-Za-z0-9\-]", "", s)
        if s_clean in self.symbol_to_hgnc:
            return {"symbol": s_clean, "hgnc_id": self.symbol_to_hgnc[s_clean] or None,
                    "grounding": {"status": "grounded", "confidence": 0.98, "method": self.source, "candidates": []}}
        up = s_clean.upper()
        if up in self.symbol_to_hgnc:
            return {"symbol": up, "hgnc_id": self.symbol_to_hgnc[up] or None,
                    "grounding": {"status": "grounded", "confidence": 0.95, "method": self.source + "-case", "candidates": []}}
        if s_clean in self.alias_to_symbol:
            cur = self.alias_to_symbol[s_clean]
            return {"symbol": cur, "hgnc_id": self.symbol_to_hgnc.get(cur) or None,
                    "grounding": {"status": "grounded", "confidence": 0.9, "method": "hgnc-alias", "candidates": [{"id": s_clean, "label": "alias/previous symbol", "score": 0.9}]}}
        return {"symbol": s, "hgnc_id": None, "grounding": {"status": "unresolved", "confidence": None, "method": self.source, "candidates": []}}
