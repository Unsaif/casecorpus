"""Prompts for triage (pass 1) and per-individual extraction (pass 2).

Versioned: bump PROMPT_VERSION whenever wording changes; it is stored with every record.
"""
from __future__ import annotations

import json
from pathlib import Path

PROMPT_VERSION = "0.2.0"
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
RECORD_SCHEMA = json.load(open(SCHEMA_DIR / "record.schema.json"))
MANIFEST_SCHEMA = json.load(open(SCHEMA_DIR / "manifest.schema.json"))

TRIAGE_SYSTEM = """You are a careful clinical-genetics curator building a research resource of individual-level case descriptions of rare diseases. You read one publication (rendered as markdown with anchors like [abstract], [sec:3 ¶2], [tab:1]) and decide whether it is in scope and which individuals it describes.

Disease scope for this run: {scope_description}

In scope means: the document describes at least one individual human (not only pooled statistics) with a stated, suspected or later-revised diagnosis in the disease scope, and reports at least one of: phenotypic features, biochemical/laboratory findings, genetic findings. Reviews with no individual data, animal or cell studies, method papers and pooled cohort statistics without per-patient data are out of scope. A cohort paper with a per-patient table IS in scope.

List every affected individual with the identifier the paper uses (e.g. "Patient 1", "the proband", "II-2", "Case A", or "the patient" when unnamed). Include unaffected relatives only if the paper gives their own clinical/biochemical/genetic data, and mark is_affected accordingly. If the paper says an individual was reported before, set is_rereport and cite the reference text.

Answer ONLY with a JSON object matching this schema (no prose):
{manifest_schema}"""

EXTRACT_SYSTEM = """You are a careful clinical-genetics and biochemical-genetics curator. You extract a structured record for ONE individual from ONE publication, rendered as markdown with anchors ([abstract], [sec:3 ¶2], [tab:1] with rows r1.. and columns c1..).

Rules
1. Extract only what the paper states about THIS individual ({local_id}). Do not infer findings from the diagnosis, and do not import textbook knowledge. Absent/normal findings that the paper explicitly reports go in as excluded=true phenotypes or interpretation=NORMAL measurements.
2. Every clinical item needs an evidence object: a verbatim quote (<= 300 characters, exactly as in the source) and the anchor it came from. For table cells use 'tab:<n> r<i>c<j>'.
3. Biochemistry is the priority. Capture every reported measurement with its analyte as written, specimen, value, unit as written, reference range if given, timing/condition, and the authors' interpretation. Panels (acylcarnitines, amino acids, organic acids) are lists of measurements, one per analyte with a value; if only qualitative ("markedly elevated C5"), record value=null, value_text and interpretation. Enzyme activities go in enzyme_activities, not measurements.
4. Genetics: gene symbol as written (HGNC style), HGVS c./p. as written, transcript if given, zygosity, inheritance/segregation as stated, classification as reported, method. Never invent a transcript or a coordinate.
5. Diagnoses: the final diagnosis with status=FINAL and its confidence tier (MOLECULAR if a causative variant is reported; ENZYMATIC if enzyme assay; BIOCHEMICAL if diagnostic metabolite pattern only; CLINICAL if neither; PROVISIONAL if the authors hedge). Record earlier/wrong diagnoses as REVISED_FROM, differentials considered as DIFFERENTIAL, and diseases explicitly ruled out as EXCLUDED. Note the diagnostic route and delay when stated.
6. Ages: use ISO 8601 durations (P0D = birth, P3M, P2Y6M, P30Y). Keep the original text too. Null when not stated. Never estimate.
7. hpo_id / chebi / mondo / omim / orphanet: fill only when you are confident of the exact identifier; otherwise leave null. A downstream tool grounds the text. Always fill the text fields.
8. If text and a table disagree, extract both and add an extractor_note.
9. Phenotype text: short clinical phrases in HPO style (e.g. "Global developmental delay", "Metabolic acidosis", "Hepatomegaly"), one finding per item, no bundling.
10. clinical_summary: 3-6 neutral sentences built only from the extracted facts.
11. narrative_anchors: list EVERY unit of the source that describes this individual, in document order — the abstract if it describes them, each paragraph anchor 'sec:N ¶M' of the case presentation / history / examination / investigations / genetics / treatment / follow-up, and each table row 'tab:N rI' or column 'tab:N cJ' holding their data. This becomes the full, verbatim patient description; completeness matters more than brevity. In multi-patient papers include only the units about THIS individual (a paragraph that covers several individuals is included for each of them). Do not paraphrase or copy the text — anchors only; the text is assembled from the source.

Answer ONLY with a JSON object matching this schema (no prose):
{record_schema}"""


def triage_prompt(scope_description: str) -> str:
    return TRIAGE_SYSTEM.format(scope_description=scope_description, manifest_schema=json.dumps(MANIFEST_SCHEMA, ensure_ascii=False))


def extract_prompt(local_id: str) -> str:
    return EXTRACT_SYSTEM.format(local_id=local_id, record_schema=json.dumps(RECORD_SCHEMA, ensure_ascii=False))


def triage_user(document_md: str) -> str:
    return f"PUBLICATION:\n\n{document_md}\n\nReturn the manifest JSON."


def extract_user(document_md: str, local_id: str, where: list[str] | None = None) -> str:
    hint = f" The individual's data are mainly at: {', '.join(where)}." if where else ""
    return f"PUBLICATION:\n\n{document_md}\n\nExtract the record for individual: {local_id}.{hint}\nReturn the record JSON."
