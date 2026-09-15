# Pilot 2 — human-verified evaluation on ten IMDs

Decided 15 Sept 2026 (Tim + PI, with Claude). Purpose: measure, per field, how well the extraction pipeline
turns papers into records, before anything is harvested at scale; produce the gold set that becomes the
regression suite for every later prompt or model change; and settle the cheap-vs-strong model question
on measured error rates rather than on principle.

## 1. Scope

Ten inborn errors of metabolism chosen for coverage, not convenience (`casecorpus/data/pilot2_diseases.tsv`):

| key | disease | gene(s) | why it is in |
|---|---|---|---|
| otc | ornithine transcarbamylase deficiency | OTC | urea cycle; X-linked with symptomatic heterozygous females; hyperammonemia at any age; gene symbol ambiguous in text |
| mma | methylmalonic acidemia | MMUT, MMAA, MMAB, MCEE, MMADHC, MMACHC, … | organic acidemia; biochemistry-rich; mut0/mut- and cbl complementation groups — tests disease-vs-gene linkage |
| pa | propionic acidemia | PCCA, PCCB | sibling disorder of MMA — tests disambiguation of closely related diseases |
| pku | phenylketonuria | PAH | largest IEM literature; newborn-screening route; BH4 responsiveness; maternal PKU |
| msud | maple syrup urine disease | BCKDHA, BCKDHB, DBT | BCAA / allo-isoleucine measurements; classic vs intermediate vs intermittent |
| mcadd | MCAD deficiency | ACADM | fatty-acid oxidation; acylcarnitine panels (C8); newborn screening; sudden-death presentations |
| gsd1a | glycogen storage disease type Ia | G6PC1 | hypoglycaemia / lactate / lipids; long-term complications; type I vs Ia/Ib disambiguation |
| gaucher | Gaucher disease | GBA1 | lysosomal; enzyme activity + biomarkers (chitotriosidase, lyso-Gb1); ERT/SRT response; types I–III |
| fabry | Fabry disease | GLA | X-linked; late-onset cardiac/renal variants; diagnostic odyssey; overlaps common disease |
| wilson | Wilson disease | ATP7B | copper biochemistry; heavy overlap with common hepatic/psychiatric/neurological disease |

Each disease is one PubMed query — (MeSH descriptor OR disease names OR unambiguous gene symbols)[tiab] AND
Case Reports[pt] — and one document set `pilot2:<key>` in the catalogue. Sanity-check the counts first with
`casecorpus count-diseases`.

## 2. Sample

`casecorpus sample` draws 25 documents per disease (250 total), deterministically (seed 1), English only:
about 72 % with full text expected (in Europe PMC / OA / has a PMCID) and 28 % abstract-only, so that
abstract-only records are measured too; within each half at least two documents per year band
(≤1999, 2000–2014, ≥2015) and at least four series / small-series papers per disease, the rest proportional
to availability. Document kind is a title/pub-type heuristic (single_case, small_series, series,
case_plus_review, other) used only for stratification; the triage manifest records the real
document_type and, new in prompt 0.3.0, a `case_type` per individual (typical / atypical / novel
variant / treatment / diagnostic odyssey / complication / screening-detected) so reporting bias is on record.

Expected yield: ~250 documents → ~400 records (1.6 per full-text paper in pilot 1).

## 3. Extraction

Reference extraction in the Cowork session by Claude (prompt 0.3.0; triage + one record per affected
individual; verbatim narrative by anchor; every quote verified against the rendered source). Model routing is
NOT tested in this pass; once a key exists the same 250 documents are run through `casecorpus extract` with
Sonnet / Opus / Fable and scored against the gold set produced here.

## 4. Human verification (Tim's team)

`casecorpus review-export --only-set pilot2-sample --reviewers a,b,c` writes one workbook per reviewer batch
(8 records each, default), with a quarter of each batch also assigned to a second reviewer for agreement.
Target: ~80 records fully reviewed, ~20 double-reviewed; the rest spot-checked through the case sheets.

Per record the reviewer works through the Facts sheet: one row per extracted item (subject, family,
phenotype, measurement, enzyme activity, genetic finding, diagnosis, treatment, outcome) with its value,
grounding, verbatim quote, anchor and automatic verification status. Verdicts: Correct · Partly correct ·
Wrong value · Wrong grounding · Wrong patient · Not in paper · Unsure, plus a Correction and a Comment. The
Missed sheet takes facts the paper states that the record lacks (this is the recall measurement — be complete
for phenotypes, measurements, genetic findings and diagnoses). The Record sheet asks whether the FINAL
diagnosis and its tier are right, whether the verbatim narrative covers the whole description of that
individual and whether it leaks text about others, and an overall 1–5. The Paper text sheet shows the
document exactly as the extractor saw it, one row per anchored unit, with the records whose narrative
includes each unit, so quotes can be checked without opening the PDF (the PDF is still needed for Missed).

Annotation rules: judge against the paper only (not against what a clinician would expect); an item the
paper states about another individual is Wrong patient, not Not in paper; a normal/absent finding recorded
as excluded/NORMAL is Correct if the paper states it; grounding is judged on the id/label shown, and a
missing grounding on a correct text is Correct (grounding coverage is measured separately).

## 5. Metrics (`casecorpus review-import`)

Per group and overall: precision strict (Correct / reviewed, Unsure excluded) and lenient (Correct + Partly);
recall strict and lenient from Missed rows; grounding error rate; attribution (Wrong patient) rate;
inter-reviewer exact-verdict agreement and Cohen's kappa on Correct-vs-not for double-reviewed facts
(the human ceiling the model is judged against); record-level diagnosis, tier, narrative completeness,
narrative leakage, quality. Written to `gold/decisions.jsonl`, `gold/missed.jsonl`, `gold/records.jsonl`,
`gold/metrics.json|md`. The gold files are committed to the repo and re-scored automatically whenever the
prompt or the model changes.

Decision rules agreed in advance: a field with precision ≥ 0.95 and recall ≥ 0.90 on full-text papers is
"release quality"; below that it ships flagged. A cheaper model is allowed on a document tier only if,
on the same documents, it loses < 2 points of precision and < 3 of recall on measurements, genetic
findings and diagnoses relative to the reference — otherwise routing stays on the strong model.

## 6. Steps and owners

1. Tim (his machine): install/update, `count-diseases`, `harvest-diseases`, `enrich`, `sample`,
   `fulltext --only-set pilot2-sample`, `prepare --only-set pilot2-sample`; send back the catalogue and
   `work/` (runbook: `docs/pilot2-runbook.md`).
2. Claude: triage + extraction in-session, `ingest`, `review-export`; send workbooks back.
3. Tim's team: review; return the workbooks.
4. Claude: `review-import`, metrics, prompt fixes where the errors are systematic, re-score; write up.
5. When an API key exists: model comparison on the same 250 documents.
