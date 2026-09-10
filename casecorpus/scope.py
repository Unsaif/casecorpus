"""Disease scope: a versioned list of disorders (with IDs, synonyms and genes) that defines
what "in scope" means. Built from MONDO (subtree under a root class) plus HPO's
genes_to_disease annotations. The output is data, checked into the working directory,
so a corpus can always be traced back to the exact scope that produced it.

Default root: MONDO:0019052 "inborn errors of metabolism".

Outputs (scope/):
  scope.json   {version, root, diseases:[{mondo, label, synonyms, omim:[], orphanet:[], genes:[]}]}
  scope.tsv    flat view
  terms.txt    disease names + synonyms usable in PubMed [tiab] queries (deduplicated, cleaned)
  genes.txt    gene symbols in scope
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

MONDO_PREFIX = "http://purl.obolibrary.org/obo/MONDO_"
DEFAULT_ROOT = "MONDO:0019052"


def _curie(iri: str) -> str:
    if iri.startswith(MONDO_PREFIX):
        return "MONDO:" + iri[len(MONDO_PREFIX):]
    return iri


def load_mondo(path: Path) -> tuple[dict[str, dict], dict[str, set[str]]]:
    """Return (nodes by CURIE, children map by CURIE) for MONDO obographs JSON."""
    g = json.load(open(path))["graphs"][0]
    nodes: dict[str, dict] = {}
    for n in g["nodes"]:
        if n.get("type") != "CLASS":
            continue
        cid = _curie(n["id"])
        if not cid.startswith("MONDO:"):
            continue
        lbl = n.get("lbl", "")
        if lbl.lower().startswith("obsolete"):
            continue
        meta = n.get("meta", {})
        syns = [s["val"] for s in meta.get("synonyms", []) if s.get("pred") in ("hasExactSynonym", "hasRelatedSynonym", "hasNarrowSynonym")]
        xrefs = [x["val"] for x in meta.get("xrefs", [])]
        nodes[cid] = {
            "mondo": cid,
            "label": lbl,
            "synonyms": sorted(set(syns)),
            "omim": sorted({x for x in xrefs if x.startswith("OMIM:")}),
            "orphanet": sorted({x.replace("Orphanet:", "ORPHA:") for x in xrefs if x.startswith("Orphanet:")}),
            "definition": (meta.get("definition") or {}).get("val"),
        }
    children: dict[str, set[str]] = defaultdict(set)
    for e in g["edges"]:
        if e.get("pred") == "is_a":
            sub, obj = _curie(e["sub"]), _curie(e["obj"])
            if sub in nodes and obj in nodes:
                children[obj].add(sub)
    return nodes, children


def descendants(root: str, children: dict[str, set[str]]) -> set[str]:
    seen, stack = set(), [root]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(children.get(n, ()))
    return seen


def load_genes_to_disease(path: Path) -> dict[str, set[str]]:
    """disease_id (OMIM:/ORPHA:) -> set of gene symbols (HPO genes_to_disease.txt)."""
    out: dict[str, set[str]] = defaultdict(set)
    with open(path) as fh:
        header = fh.readline().rstrip("\n").split("\t")
        idx = {h: i for i, h in enumerate(header)}
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < len(header):
                continue
            did = parts[idx["disease_id"]]
            sym = parts[idx["gene_symbol"]]
            if did.startswith("ORPHA:") or did.startswith("OMIM:"):
                out[did].add(sym)
    return out


_BAD_TERM = re.compile(r"^(?:[A-Z0-9\-]{1,4}|.{0,4})$")  # too short/ambiguous for a [tiab] query


def _clean_term(t: str) -> str | None:
    t = t.strip()
    t = re.sub(r"\s*\(.*?\)\s*$", "", t)  # drop trailing parenthetical qualifiers
    if _BAD_TERM.match(t):
        return None
    if re.search(r"[\"\[\]]", t):
        return None
    return t


def build_scope(mondo_json: Path, genes_to_disease: Path, out_dir: Path, root: str = DEFAULT_ROOT,
                exclude_roots: tuple[str, ...] = ()) -> dict[str, Any]:
    nodes, children = load_mondo(mondo_json)
    if root not in nodes:
        raise SystemExit(f"root {root} not found in MONDO")
    ids = descendants(root, children)
    for ex in exclude_roots:
        ids -= descendants(ex, children)
    g2d = load_genes_to_disease(genes_to_disease)

    diseases = []
    for cid in sorted(ids):
        n = nodes[cid]
        genes: set[str] = set()
        for did in n["omim"] + n["orphanet"]:
            genes |= g2d.get(did, set())
        diseases.append({**n, "genes": sorted(genes)})

    version = re.search(r"releases/(\d{4}-\d{2}-\d{2})", json.load(open(mondo_json))["graphs"][0].get("meta", {}).get("version", "") or "")
    scope = {
        "root": root,
        "root_label": nodes[root]["label"],
        "mondo_release": version.group(1) if version else None,
        "excluded_roots": list(exclude_roots),
        "n_diseases": len(diseases),
        "diseases": diseases,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scope.json").write_text(json.dumps(scope, indent=1, ensure_ascii=False))

    with open(out_dir / "scope.tsv", "w") as fh:
        fh.write("mondo\tlabel\tomim\torphanet\tgenes\tn_synonyms\n")
        for d in diseases:
            fh.write(f"{d['mondo']}\t{d['label']}\t{'|'.join(d['omim'])}\t{'|'.join(d['orphanet'])}\t{'|'.join(d['genes'])}\t{len(d['synonyms'])}\n")

    terms: set[str] = set()
    genes_all: set[str] = set()
    for d in diseases:
        for t in [d["label"], *d["synonyms"]]:
            c = _clean_term(t)
            if c:
                terms.add(c)
        genes_all |= set(d["genes"])
    (out_dir / "terms.txt").write_text("\n".join(sorted(terms, key=str.lower)) + "\n")
    (out_dir / "genes.txt").write_text("\n".join(sorted(genes_all)) + "\n")
    scope["n_terms"], scope["n_genes"] = len(terms), len(genes_all)
    return scope


def load_scope(scope_dir: Path) -> dict[str, Any]:
    return json.load(open(scope_dir / "scope.json"))


class ScopeMatcher:
    """Tag documents with scope entries by matching MeSH headings, disease names/synonyms and gene symbols
    in title/abstract/keywords. Deliberately simple (exact, case-insensitive, word-bounded); the triage
    model makes the real in/out decision."""

    def __init__(self, scope: dict[str, Any]):
        self.by_term: dict[str, set[str]] = defaultdict(set)
        self.by_gene: dict[str, set[str]] = defaultdict(set)
        self.by_omim: dict[str, str] = {}
        for d in scope["diseases"]:
            for t in [d["label"], *d["synonyms"]]:
                c = _clean_term(t)
                if c:
                    self.by_term[c.lower()].add(d["mondo"])
            for g in d["genes"]:
                self.by_gene[g].add(d["mondo"])
            for o in d["omim"]:
                self.by_omim[o] = d["mondo"]
        # one big alternation, longest first, word-bounded
        terms = sorted(self.by_term, key=len, reverse=True)
        self._term_re = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(t) for t in terms) + r")(?![A-Za-z0-9])", re.I) if terms else None
        genes = sorted(self.by_gene, key=len, reverse=True)
        self._gene_re = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(g) for g in genes) + r")(?![A-Za-z0-9])") if genes else None

    def tag(self, text: str) -> dict[str, list[str]]:
        tags: dict[str, set[str]] = {"mondo": set(), "genes": set(), "terms": set()}
        if not text:
            return {k: [] for k in tags}
        if self._term_re:
            for m in self._term_re.finditer(text):
                t = m.group(1).lower()
                tags["terms"].add(t)
                tags["mondo"] |= self.by_term[t]
        if self._gene_re:
            for m in self._gene_re.finditer(text):
                g = m.group(1)
                tags["genes"].add(g)
                tags["mondo"] |= self.by_gene[g]
        return {k: sorted(v) for k, v in tags.items()}
