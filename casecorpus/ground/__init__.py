"""Deterministic grounding of extracted text to ontology identifiers.

Grounders never overwrite a model-supplied id that validates; they fill missing ids, verify
supplied ones (id exists and label agrees), and attach candidates + confidence so a curator can
see why. Unresolvable items are kept with grounding.status = 'unresolved'.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .analytes import AnalyteGrounder
from .genes import GeneGrounder
from .hpo import HPOGrounder
from .mondo import MondoGrounder
from .units import normalise_unit


class Grounders:
    def __init__(self, ontologies: Path, scope_dir: Path | None = None):
        self.hpo = HPOGrounder(ontologies / "hp.json")
        self.mondo = MondoGrounder(ontologies / "mondo.json", cache=ontologies / "mondo_index.json")
        self.genes = GeneGrounder(ontologies)
        self.analytes = AnalyteGrounder(ontologies)

    def ground_record(self, rec: dict[str, Any]) -> dict[str, Any]:
        for ph in rec.get("phenotypes", []):
            g = self.hpo.ground(ph.get("text", ""), ph.get("hpo_id"))
            ph["hpo_id"], ph["hpo_label"], ph["grounding"] = g["id"], g["label"], g["grounding"]
        for m in rec.get("measurements", []):
            g = self.analytes.ground(m.get("analyte_text", ""), (m.get("analyte") or {}).get("chebi"))
            m["analyte"] = {**(m.get("analyte") or {}), **{k: v for k, v in g["ids"].items() if v}}
            m["grounding"] = g["grounding"]
            if m.get("unit"):
                m["unit_ucum"] = normalise_unit(m["unit"])
        for gf in rec.get("genetic_findings", []):
            g = self.genes.ground(gf.get("gene_symbol", ""))
            gf["gene_symbol"], gf["hgnc_id"], gf["grounding"] = g["symbol"], g["hgnc_id"], g["grounding"]
        for ea in rec.get("enzyme_activities", []):
            if ea.get("gene_symbol"):
                g = self.genes.ground(ea["gene_symbol"])
                ea["gene_symbol"] = g["symbol"]
        for dx in rec.get("diagnoses", []):
            g = self.mondo.ground(dx.get("disease_text", ""), dx.get("disease") or {})
            dx["disease"], dx["grounding"] = g["disease"], g["grounding"]
        return rec
