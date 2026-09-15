# casecorpus

Large-scale retrieval and structured extraction of rare-disease **case descriptions** from the
literature — phenotypes (HPO), **biochemistry** (analyte / specimen / value / unit / reference range),
genetics (HGNC / HGVS), diagnosis with a confidence tier, treatment and outcome — one record per
individual, every fact with a verbatim evidence span, output as GA4GH Phenopackets plus flat tables.

First scope: inborn errors of metabolism (MONDO:0019052 subtree). The machinery is scope-agnostic.

## Pipeline

```
scope ──▶ harvest (PubMed) ──▶ enrich (Europe PMC) ──▶ fulltext (tiers) ──▶ prepare
      ──▶ extract (triage + per-individual) ──▶ ingest (ground + validate + phenopacket)
      ──▶ link (same individual across papers) ──▶ export (parquet/csv)
```

| Stage | What it does | Where the truth lives |
|---|---|---|
| `scope` | disease list with MONDO/OMIM/Orphanet ids, synonyms and genes | `scope/scope.json` |
| `harvest` | PubMed E-utilities via the history server; MeSH seed + title/abstract lag layer; Europe PMC full-text case-section layer | `catalogue.sqlite` → `documents` |
| `enrich` | open-access flag, licence, inEPMC, PMCID | `documents` |
| `fulltext` | tier 1 Europe PMC / PMC JATS XML (+ supplements) → tier 2 Unpaywall → tier 3 publisher TDM (Elsevier / Wiley / Springer, needs tokens) → tier 4 abstract only | `raw/<pmid>/`, `retrieval` |
| `prepare` | JATS → markdown with anchors (`[sec:3 ¶2]`, `[tab:1 r4c2]`), tables as grids | `work/<pmid>/input.md` |
| `extract` | pass 1 triage manifest (in scope? which individuals? where?), pass 2 one JSON record per individual against `schema/record.schema.json` | `work/<pmid>/manifest.json`, `record_*.json` |
| `ingest` | deterministic grounding (HPO, MONDO+aliases, HGNC, ChEBI/VMH analytes, UCUM units), validation, phenopacket v2 | `individuals`, `records/<pmid>/` |
| `link` | same-individual links across papers (variant + sex + birth year + authors); links, never merges | `links` |
| `export` | `documents, individuals, phenotypes, measurements, enzyme_activities, genetic_findings, diagnoses, treatments, links` | `export/*.parquet|csv` |

## Install and run

```bash
pip install -e .                      # or: pip install -e '.[llm]' for the API extractor
export CASECORPUS_HOME=~/casecorpus-work
export NCBI_EMAIL=you@university.ie   # NCBI_API_KEY=... makes harvesting 3x faster
casecorpus fetch-ontologies           # HPO, MONDO, HGNC, ChEBI names (GitHub / EBI / HGNC)
casecorpus scope                      # 2,046 IEM diseases from MONDO 2026-09 + HPO annotations
casecorpus count '("Metabolism, Inborn Errors"[MeSH]) AND ("Case Reports"[pt])'   # 41,933 on 10 Sept 2026
casecorpus harvest                    # seed layer (all years)
casecorpus harvest-lag                # recent, un-indexed papers (title/abstract terms + genes)
casecorpus harvest-recall             # Europe PMC papers with a case section, any pub type
casecorpus enrich
casecorpus fulltext                   # tiers 1-3 as credentials allow
casecorpus prepare --where "fulltext_tier='epmc'" --limit 200
casecorpus extract --engine claude    # needs ANTHROPIC_API_KEY; or leave work/ for a Cowork session
casecorpus ingest && casecorpus link && casecorpus export
casecorpus status
```

The extraction step is engine-agnostic: anything that writes `manifest.json` and `record_<slug>.json`
into `work/<pmid>/` (the Claude API runner, a Cowork session, a person) feeds the same ingest.
`scripts/validate_work.py <work_dir>/<pmid>` checks a work item, including that every evidence quote
occurs verbatim in `input.md`.

## Record schema (per individual)

`schema/record.schema.json` — GA4GH Phenopacket v2 at the core, plus what it lacks:
`measurements` (analyte text → ChEBI/VMH, specimen, value, unit, UCUM, reference range, timing,
condition, panel, method, interpretation as reported), `enzyme_activities`, `diagnoses` with
`status` (FINAL / PROVISIONAL / DIFFERENTIAL / REVISED_FROM / EXCLUDED) and `confidence_tier`
(MOLECULAR / ENZYMATIC / BIOCHEMICAL / CLINICAL / PROVISIONAL), diagnostic route and delay,
`treatments`, `outcome`, `family`, and an `evidence {quote, anchor}` on every fact.

## Pilot (10 Sept 2026)

12 open-access IEM case reports (2026) fetched through PMC, 11 extracted by Claude in a Cowork
session → 18 individuals, 422 phenotypes (60 % auto-grounded to HPO, rest kept as candidates),
482 measurements (75 % with a numeric value, 46 % with a reference range; 33 % auto-grounded to an
analyte id with the small built-in lexicon — ChEBI names + VMH raise this), 31 variants (100 %
gene-grounded), 52 diagnoses (56 % auto-grounded), 18/18 valid phenopackets. See
`docs/pilot-2026-09-10.md`.

## Evaluation: pilot 2 (human-verified, ten IMDs)

`docs/pilot2-protocol.md` is the design (ten diseases, stratified 25-document sample per disease, review
workbooks, per-field precision/recall, agreement, decision rules); `docs/pilot2-runbook.md` is what to run.
Commands: `count-diseases`, `harvest-diseases` (one PubMed query per disease → document sets `pilot2:<key>`),
`sample` (→ set `pilot2-sample` + TSV), `fulltext/prepare --only-set`, `review-export` (one workbook per
reviewer batch: Facts with verdict dropdowns, Missed, Record, Paper text), `review-import` (→ `gold/` +
`metrics.md`). Triage manifests carry `document_type` and a per-individual `case_type` (prompt 0.3.0) so
reporting bias is recorded.

## External: PMC-Patients overview

`casecorpus pmc-patients-overview PMC-Patients-V2.json.gz` streams the 250k-summary PMC-Patients
dataset (CC BY-NC-SA 4.0) and writes `external/pmc_patients_overview/`: one row per summary
(`patients.parquet` with the text, `patients.csv` without) plus demographics, approximate year
(interpolated from the PMID), licence group, patients-per-article, summary length, diseases named in
titles (lexical MONDO match) and IEM flags (scope disease in title or scope gene symbol in text).
`python scripts/pmc_patients_workbook.py <overview_dir> <out.xlsx>` turns that into a workbook whose
aggregates are formulas over the full table. Roughly 10 min for the overview, a minute for the workbook.

## Known limitations / next

- Full-text coverage: only ~13 % of IEM case reports have PMC full text (25 % since 2010); the
  publisher TDM tier needs institutional tokens from the library (`docs/library-tdm-request.md`).
- Analyte lexicon is small until `chebi_names.tsv.gz` / `vmh_metabolites.tsv` are present.
- Gene aliases (old symbols such as MUT) need `hgnc_complete_set.txt`.
- Per-record `confidence` is a crude heuristic; a second independent extraction pass and
  field-level agreement is the intended signal.
- Validator HGVS regexes are deliberately loose; VariantValidator integration is a to-do.
- Cross-paper linking only fires when a variant is shared; re-report statements in manifests are
  not yet used.
