# Pilot 2 runbook (Tim's machine)

Everything below talks to free public APIs (NCBI E-utilities, Europe PMC, Unpaywall). Nothing costs money.
Times are for a laptop on a normal connection; an NCBI API key makes the harvest ~3x faster
(https://www.ncbi.nlm.nih.gov/account/settings/ → API Key Management).

## 0. One-off setup (or update)

```bash
cd ~/Documents/casecorpus
python3 -m venv .venv && source .venv/bin/activate      # or: source .venv/bin/activate if it exists
pip install -e .
export CASECORPUS_HOME=~/Documents/casecorpus/casecorpus-work
export NCBI_EMAIL=you@universityofgalway.ie
export NCBI_API_KEY=...                                   # optional
casecorpus fetch-ontologies                               # skips files already present
casecorpus scope                                          # skips nothing; ~1 min; safe to re-run
casecorpus status
```

`casecorpus status` should list the pilot-1 documents (20) and records (18); the new `document_sets` table
appears automatically.

## 1. Check the disease queries (30 s)

```bash
casecorpus count-diseases
```

Prints, per disease, the PubMed hit count for the case-report query, for all publication types, and for the
MeSH clause alone. Expect hundreds to a few thousand case reports per disease (PKU and Wilson the largest).
If a MeSH-only count is 0 the descriptor name in `casecorpus/data/pilot2_diseases.tsv` is wrong — tell me;
the tiab terms still work.

## 2. Harvest the ten disease sets (5–15 min)

```bash
casecorpus harvest-diseases
casecorpus status            # by_layer and sets: pilot2:otc … pilot2:wilson
```

Fetches PubMed metadata for every case report matching each query and tags it with its set. A document
matching two queries (MMA/PA papers often do) is in both sets. Re-running only updates.

## 3. Enrich with Europe PMC flags (5–10 min)

```bash
casecorpus enrich
```

Adds OA / licence / in-Europe-PMC / PMCID per document; the sampler uses these to decide which documents can
be expected to have full text.

## 4. Draw the sample (seconds)

```bash
casecorpus sample                  # 25 per disease, seed 1, English only
```

Writes `casecorpus-work/pilot2-sample.tsv` and registers the set `pilot2-sample`. The JSON it prints shows
the split by disease, full-text expectation, year band and kind. If a disease has fewer than 25 documents
matching the strata it takes what exists. Options: `--per-set`, `--seed`, `--fulltext-share`,
`--min-series`, `--all-languages`, `--exclude-sets`.

## 5. Fetch full text for the sample only (10–30 min)

```bash
casecorpus fulltext --only-set pilot2-sample
casecorpus fulltext --only-set pilot2-sample --tiers pmc --retry-failed     # second pass for very recent papers
```

Tiers: Europe PMC XML → PMC efetch → Unpaywall (PDF/HTML) → publisher TDM (only if tokens are set).
Documents where nothing works stay abstract-only, which is a stratum we want anyway.

## 6. Render the work items (1 min)

```bash
casecorpus prepare --only-set pilot2-sample
casecorpus status
```

Creates `casecorpus-work/work/<pmid>/input.md` + prompts + meta.json for every sampled document.

## 7. Send it back

```bash
cd ~/Documents/casecorpus
tar czf pilot2-handback.tgz casecorpus-work/catalogue.sqlite casecorpus-work/pilot2-sample.tsv casecorpus-work/work casecorpus-work/raw
ls -la pilot2-handback.tgz
```

Leave `pilot2-handback.tgz` in `~/Documents/casecorpus` (the connected folder) and tell me; I stage it from
there. Expect tens of MB. I then run triage + extraction in the session, ingest, and hand you the review
workbooks in `casecorpus-work/review/` with `assignments.tsv` saying who reviews what.

## 8. After the review

Put the filled workbooks back into `casecorpus-work/review/` (same file names) and tell me; or run

```bash
casecorpus review-import          # reads casecorpus-work/review/review_*.xlsx → casecorpus-work/gold/
cat casecorpus-work/gold/metrics.md
```

## If something fails

- `count-diseases` / `harvest-diseases` HTTP 429 or 5xx: the client backs off and retries; just re-run.
- `fetch-ontologies` ChEBI failure: optional; grounding works without it.
- Anything else: paste the traceback here.
