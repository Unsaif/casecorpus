"""casecorpus command-line interface.

Typical run (on a machine with internet access):

  export NCBI_EMAIL=you@university.ie NCBI_API_KEY=...      # optional key, faster
  casecorpus fetch-ontologies                                # HPO, MONDO, HGNC, ChEBI names
  casecorpus scope                                           # IEM disease list from MONDO + HPO annotations
  casecorpus harvest                                         # PubMed seed layer (MeSH IEM x Case Reports)
  casecorpus harvest-lag                                     # recent papers missed by MeSH
  casecorpus harvest-recall                                  # Europe PMC full-text case sections
  casecorpus enrich                                          # OA / licence / inEPMC flags
  casecorpus fulltext                                        # tiers: Europe PMC -> Unpaywall -> publisher TDM
  casecorpus prepare --limit 50                              # render work/<pmid>/input.md + prompts
  casecorpus extract --engine claude                         # or leave for a Cowork session / manual
  casecorpus ingest                                          # ground + validate + phenopackets
  casecorpus link                                            # cross-paper same-individual links
  casecorpus export                                          # parquet/csv tables
  casecorpus status

Pilot / evaluation (docs/pilot2-protocol.md, docs/pilot2-runbook.md):

  casecorpus count-diseases                                  # PubMed counts per pilot disease query
  casecorpus harvest-diseases                                # one harvest per disease -> sets pilot2:<key>
  casecorpus sample                                          # stratified 25/disease -> set pilot2-sample
  casecorpus fulltext --only-set pilot2-sample
  casecorpus prepare --only-set pilot2-sample
  casecorpus review-export --only-set pilot2-sample --reviewers a,b   # workbooks for human verification
  casecorpus review-import                                   # filled workbooks -> gold/ + metrics
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .config import Settings
from .db import Catalogue

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

ONTOLOGY_URLS = {
    "hp.json": "https://github.com/obophenotype/human-phenotype-ontology/releases/latest/download/hp.json",
    "phenotype.hpoa": "https://github.com/obophenotype/human-phenotype-ontology/releases/latest/download/phenotype.hpoa",
    "genes_to_disease.txt": "https://github.com/obophenotype/human-phenotype-ontology/releases/latest/download/genes_to_disease.txt",
    "mondo.json": "https://github.com/monarch-initiative/mondo/releases/latest/download/mondo.json",
    "hgnc_complete_set.txt": "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt",
    "chebi_names.tsv.gz": "https://ftp.ebi.ac.uk/pub/databases/chebi/flat_files/names.tsv.gz",
    "chebi_compounds.tsv.gz": "https://ftp.ebi.ac.uk/pub/databases/chebi/flat_files/compounds.tsv.gz",
}


def _ctx(home: Optional[str]) -> tuple[Settings, Catalogue]:
    s = Settings(home=Path(home).resolve()) if home else Settings()
    s.ensure()
    return s, Catalogue(s.db_path)


def _run(cat: Catalogue, command: str, args: dict, fn):
    run_id = f"{command}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    cat.start_run(run_id, command, args, __version__)
    result = fn()
    cat.finish_run(run_id, result)
    typer.echo(json.dumps(result, indent=1, default=str))
    return result


@app.command()
def fetch_ontologies(home: Optional[str] = None, only: Optional[str] = None, force: bool = False):
    """Download HPO, MONDO, HGNC and ChEBI names into <home>/ontologies."""
    import requests
    s, _ = _ctx(home)
    for name, url in ONTOLOGY_URLS.items():
        if only and name != only:
            continue
        dest = s.ontologies / name
        if dest.exists() and not force:
            typer.echo(f"{name}: present")
            continue
        typer.echo(f"{name}: downloading ...")
        try:
            with requests.get(url, stream=True, timeout=600) as r:
                r.raise_for_status()
                with open(dest, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
            typer.echo(f"{name}: {dest.stat().st_size/1e6:.1f} MB")
        except Exception as e:
            typer.echo(f"{name}: FAILED ({e}) — optional, grounding works without it" if name in ("hgnc_complete_set.txt", "chebi_names.tsv.gz", "chebi_compounds.tsv.gz") else f"{name}: FAILED ({e})")


@app.command()
def scope(home: Optional[str] = None, root: str = "MONDO:0019052", exclude: Optional[str] = None):
    """Build the disease scope (default: MONDO 'inborn errors of metabolism' subtree)."""
    from .scope import build_scope
    s, cat = _ctx(home)
    ex = tuple(x.strip() for x in exclude.split(",")) if exclude else ()
    _run(cat, "scope", {"root": root, "exclude": ex},
         lambda: {k: v for k, v in build_scope(s.ontologies / "mondo.json", s.ontologies / "genes_to_disease.txt", s.scope_dir, root, ex).items() if k != "diseases"})


@app.command()
def harvest(home: Optional[str] = None, query: Optional[str] = None, layer: str = "A_seed", mindate: Optional[str] = None, limit: Optional[int] = None):
    """PubMed seed harvest through the history server (default query: MeSH IEM x Case Reports)."""
    from .harvest import DEFAULT_SEED_QUERY, harvest_seed
    s, cat = _ctx(home)
    q = query or DEFAULT_SEED_QUERY
    _run(cat, "harvest", {"query": q, "layer": layer, "mindate": mindate, "limit": limit}, lambda: harvest_seed(s, cat, q, layer, mindate, limit))


@app.command()
def harvest_lag(home: Optional[str] = None, years_back: int = 3, limit: Optional[int] = None):
    """Recent Case Reports mentioning scope diseases/genes in title/abstract but not in the MeSH seed."""
    from .harvest import harvest_lag as _hl
    s, cat = _ctx(home)
    _run(cat, "harvest-lag", {"years_back": years_back, "limit": limit}, lambda: _hl(s, cat, years_back=years_back, limit=limit))


@app.command()
def harvest_recall(home: Optional[str] = None, limit: Optional[int] = None):
    """Europe PMC full-text search: papers with a case section mentioning scope diseases (any pub type)."""
    from .harvest import harvest_epmc_case_sections
    s, cat = _ctx(home)
    _run(cat, "harvest-recall", {"limit": limit}, lambda: harvest_epmc_case_sections(s, cat, limit=limit))


@app.command()
def import_pmids(file: Path, home: Optional[str] = None, layer: str = "C_manual"):
    """Add explicit PMIDs from a text file (one per line), e.g. Phenopacket Store sources."""
    from .harvest import import_pmids as _imp
    s, cat = _ctx(home)
    pmids = [l.strip().replace("PMID:", "") for l in file.read_text().splitlines() if l.strip()]
    _run(cat, "import-pmids", {"file": str(file), "n": len(pmids)}, lambda: _imp(s, cat, pmids, layer))


@app.command()
def count_diseases(home: Optional[str] = None, file: Optional[Path] = None):
    """PubMed hit counts for each pilot disease query (sanity check before harvest-diseases)."""
    from .pilot import count_disease_sets, load_disease_sets
    s, _ = _ctx(home)
    rows = count_disease_sets(s, load_disease_sets(file))
    for r in rows:
        typer.echo(f"{r['key']:8s} case reports {r['case_reports']:6d}  all pub types {r['all_pubtypes']:6d}  mesh-only {r.get('mesh_only', '-')}")
    typer.echo(json.dumps(rows, indent=1))


@app.command()
def harvest_diseases(home: Optional[str] = None, file: Optional[Path] = None, prefix: str = "pilot2",
                     limit_per_set: Optional[int] = None, mindate: Optional[str] = None):
    """Per-disease PubMed harvest (MeSH OR names OR genes) x Case Reports; each disease becomes set <prefix>:<key>."""
    from .pilot import harvest_disease_sets, load_disease_sets
    s, cat = _ctx(home)
    sets = load_disease_sets(file)
    _run(cat, "harvest-diseases", {"file": str(file or "builtin"), "prefix": prefix, "limit_per_set": limit_per_set, "mindate": mindate},
         lambda: harvest_disease_sets(s, cat, sets, prefix, limit_per_set, mindate))


@app.command()
def sample(home: Optional[str] = None, prefix: str = "pilot2", name: str = "pilot2-sample", per_set: int = 25, seed: int = 1,
           fulltext_share: float = 0.72, min_series: int = 4, exclude_sets: Optional[str] = None, out: Optional[Path] = None,
           all_languages: bool = False):
    """Draw the stratified pilot sample from sets <prefix>:* and register it as document set <name>; writes <home>/<name>.tsv."""
    from .pilot import draw_sample, register_sample, sample_summary, write_sample
    s, cat = _ctx(home)
    set_names = sorted(n for n in cat.set_names(prefix + ":"))
    if not set_names:
        raise typer.BadParameter(f"no document sets with prefix '{prefix}:' — run harvest-diseases first")
    ex = tuple(x.strip() for x in exclude_sets.split(",")) if exclude_sets else ()
    rows = draw_sample(cat, set_names, per_set=per_set, seed=seed, fulltext_share=fulltext_share, min_series=min_series,
                       exclude_sets=ex, languages=None if all_languages else ("eng",))
    dest = out or (s.home / f"{name}.tsv")
    write_sample(rows, dest)
    n = register_sample(cat, rows, name)
    summ = sample_summary(rows)
    summ.update({"set": name, "tsv": str(dest), "registered": n})
    typer.echo(json.dumps(summ, indent=1))


@app.command()
def enrich(home: Optional[str] = None, all: bool = False, limit: Optional[int] = None):
    """Europe PMC enrichment: OA flag, licence, inEPMC, hasSuppl, PMCID."""
    from .harvest import enrich as _enrich
    s, cat = _ctx(home)
    _run(cat, "enrich", {"all": all, "limit": limit}, lambda: _enrich(s, cat, only_missing=not all, limit=limit))


@app.command()
def fulltext(home: Optional[str] = None, tiers: str = "epmc,pmc,unpaywall,publisher", limit: Optional[int] = None, retry_failed: bool = False,
             only_set: Optional[str] = None):
    """Fetch full text in tiers for documents that have none yet (--only-set restricts to a named document set)."""
    from .fulltext import fetch_all
    s, cat = _ctx(home)
    t = tuple(x.strip() for x in tiers.split(","))
    _run(cat, "fulltext", {"tiers": t, "limit": limit, "retry_failed": retry_failed, "only_set": only_set},
         lambda: fetch_all(s, cat, t, limit, retry_failed, only_set))


@app.command()
def prepare(home: Optional[str] = None, pmids: Optional[str] = None, where: str = "1=1", limit: Optional[int] = None,
            only_set: Optional[str] = None,
            scope_description: str = "inborn errors of metabolism (inherited metabolic disorders), as classified under MONDO:0019052"):
    """Render documents to work/<pmid>/input.md with prompts, ready for extraction (--only-set: a named document set)."""
    from .extract.runner import prepare as _prep
    s, cat = _ctx(home)
    if pmids:
        ids = [p.strip() for p in pmids.split(",")]
    elif only_set:
        ids = cat.set_members(only_set)
    else:
        ids = [d["pmid"] for d in cat.iter_documents(where)]
        if limit:
            ids = ids[:limit]
    items = _prep(s, cat, ids, scope_description)
    typer.echo(json.dumps({"prepared": len(items), "work_dir": str(s.work_dir)}))


@app.command()
def extract(home: Optional[str] = None, engine: str = "claude", model: str = "claude-sonnet-4-5", limit: Optional[int] = None,
            scope_description: str = "inborn errors of metabolism (inherited metabolic disorders), as classified under MONDO:0019052"):
    """Run triage + per-individual extraction on prepared work items (engine: claude | none)."""
    from .extract.runner import ClaudeEngine, WorkItem, pending, run_engine
    s, cat = _ctx(home)
    p = pending(s)
    if engine == "none":
        typer.echo(json.dumps(p, indent=1))
        return
    todo = p["need_triage"] + p["need_extract"]
    if limit:
        todo = todo[:limit]
    items = [WorkItem(pmid=pm, dir=s.work_dir / pm, input_md=s.work_dir / pm / "input.md", manifest=s.work_dir / pm / "manifest.json") for pm in todo]
    eng = ClaudeEngine(s, model=model)
    _run(cat, "extract", {"engine": engine, "model": model, "n": len(items)}, lambda: run_engine(s, cat, items, eng, scope_description))


@app.command()
def ingest(home: Optional[str] = None, pmids: Optional[str] = None):
    """Ground, validate and store extraction outputs; write phenopackets to records/."""
    from .ingest import ingest as _ingest
    s, cat = _ctx(home)
    ids = [p.strip() for p in pmids.split(",")] if pmids else None
    _run(cat, "ingest", {"pmids": ids}, lambda: _ingest(s, cat, None, ids))


@app.command()
def link(home: Optional[str] = None, min_score: float = 0.6):
    """Link records across papers that plausibly describe the same individual."""
    from .dedup import link_same_individuals
    s, cat = _ctx(home)
    _run(cat, "link", {"min_score": min_score}, lambda: link_same_individuals(cat, min_score))


@app.command()
def review_export(home: Optional[str] = None, out: Optional[Path] = None, only_set: Optional[str] = None, record_ids: Optional[str] = None,
                  reviewers: str = "reviewer1", batch_size: int = 8, double_fraction: float = 0.25, seed: int = 1):
    """Write human-review workbooks (one per reviewer batch) for extracted records: facts as rows, verdict dropdowns, paper text."""
    from .review import export_review
    s, cat = _ctx(home)
    o = out or (s.home / "review")
    ids = [r.strip() for r in record_ids.split(",")] if record_ids else None
    _run(cat, "review-export", {"out": str(o), "only_set": only_set, "reviewers": reviewers, "batch_size": batch_size, "double_fraction": double_fraction, "seed": seed},
         lambda: export_review(s, cat, o, ids, only_set, [r.strip() for r in reviewers.split(",") if r.strip()], batch_size, double_fraction, seed))


@app.command()
def review_import(home: Optional[str] = None, files: Optional[Path] = None, gold: Optional[Path] = None):
    """Read filled review workbooks (a directory or one file) into gold/ and compute precision, recall, grounding, attribution and agreement."""
    from .review import import_review
    s, cat = _ctx(home)
    src = files or (s.home / "review")
    paths = sorted(src.glob("review_*.xlsx")) if src.is_dir() else [src]
    g = gold or (s.home / "gold")
    _run(cat, "review-import", {"files": [str(p) for p in paths], "gold": str(g)}, lambda: import_review(paths, g))


@app.command()
def export(home: Optional[str] = None, fmt: str = "both"):
    """Flat tables (csv/parquet) under export/."""
    from .export import export_all
    s, cat = _ctx(home)
    _run(cat, "export", {"fmt": fmt}, lambda: export_all(s, cat, fmt))


@app.command()
def status(home: Optional[str] = None):
    """Corpus status."""
    s, cat = _ctx(home)
    from .extract.runner import pending
    st = cat.status()
    st["work"] = {k: len(v) for k, v in pending(s).items()} if s.work_dir.exists() else {}
    st["home"] = str(s.home)
    typer.echo(json.dumps(st, indent=1))


@app.command()
def pmc_patients_overview(src: Path, home: Optional[str] = None, out: Optional[Path] = None, limit: Optional[int] = None):
    """Tabular overview of the PMC-Patients dataset (JSON, JSON.gz or CSV): demographics, years, licences, diseases in titles, IEM hits."""
    from .external.pmc_patients import build_overview
    s, _ = _ctx(home)
    o = out or (s.home / "external" / "pmc_patients_overview")
    typer.echo(json.dumps(build_overview(src, o, s.ontologies, s.scope_dir, limit), indent=1))


@app.command()
def count(query: str, home: Optional[str] = None):
    """PubMed hit count for a query (sanity check for corpus definitions)."""
    from .harvest import PubMed
    s, _ = _ctx(home)
    typer.echo(PubMed(s).count(query))


if __name__ == "__main__":  # pragma: no cover
    app()
