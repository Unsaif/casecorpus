"""Human-readable case sheet per individual: the verbatim patient description first, then every
extracted fact with its quote, anchor and verification mark (✓ in anchor, ~ elsewhere in document, ✗ not found)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _mark(ev: dict[str, Any] | None) -> str:
    v = (ev or {}).get("verification") or {}
    if v.get("in_anchor"):
        return "✓"
    if v.get("in_document"):
        return "~"
    return "✗"


def _age(a: dict[str, Any] | None) -> str:
    if not a:
        return "–"
    return f"{a.get('iso8601') or '?'} ({a.get('text')})" if a.get("text") else (a.get("iso8601") or "–")


def write_case_sheet(rec: dict[str, Any], record_id: str, path: Path) -> None:
    src = rec.get("_source", {})
    nv = rec.get("_evidence_verification", {})
    subj = rec.get("subject", {})
    L: list[str] = []
    L.append(f"# {record_id}")
    L.append(f"**Source:** PMID {src.get('pmid')} · {src.get('journal')} {src.get('year')} · DOI {src.get('doi')} · licence: {src.get('license')} · tier: {src.get('tier')}")
    L.append(f"**Individual:** {rec.get('local_id')} · sex {subj.get('sex')} · onset {_age(subj.get('age_at_onset'))} · presentation {_age(subj.get('age_at_presentation'))} · last follow-up {_age(subj.get('age_at_last_followup'))} · {subj.get('vital_status')}")
    L.append(f"**Evidence verification:** {nv.get('in_anchor', 0)}/{nv.get('total', 0)} quotes found in their anchored unit, {nv.get('in_document', 0)}/{nv.get('total', 0)} anywhere in the document; narrative source: {rec.get('narrative_source')}")
    L.append("")
    L.append("## Patient description (verbatim from the source, by anchor)")
    L.append("")
    for u in rec.get("case_narrative", []):
        L.append(f"> **[{u['anchor']}]** {u['text']}")
        L.append(">")
    if not rec.get("case_narrative"):
        L.append("_(no narrative assembled)_")
    L.append("")
    L.append("## Diagnoses")
    L.append("")
    for d in rec.get("diagnoses", []):
        dis = d.get("disease") or {}
        L.append(f"- {_mark(d.get('evidence'))} **{d.get('status')}** {d.get('disease_text')} → {dis.get('label') or '–'} {dis.get('mondo') or ''} {dis.get('omim') or ''} · tier {d.get('confidence_tier')} · route {d.get('diagnostic_route') or '–'}  \n  “{(d.get('evidence') or {}).get('quote','')}” [{(d.get('evidence') or {}).get('anchor','')}]")
    L.append("")
    L.append("## Genetic findings")
    L.append("")
    for g in rec.get("genetic_findings", []):
        L.append(f"- {_mark(g.get('evidence'))} **{g.get('gene_symbol')}** {g.get('variant_hgvs_c') or g.get('variant_text') or ''} {g.get('variant_hgvs_p') or ''} · {g.get('zygosity')} · {g.get('classification_as_reported') or '–'} · {g.get('method') or '–'}  \n  “{(g.get('evidence') or {}).get('quote','')}” [{(g.get('evidence') or {}).get('anchor','')}]")
    L.append("")
    L.append("## Measurements")
    L.append("")
    L.append("| ✓ | analyte | specimen | value | unit | ref | interp | timing | quote [anchor] |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for m in rec.get("measurements", []):
        rr = m.get("reference_range") or {}
        ref = rr.get("text") or (f"{rr.get('low')}–{rr.get('high')}" if rr.get("low") is not None or rr.get("high") is not None else "")
        an = m.get("analyte") or {}
        label = an.get("label") or ""
        ev = m.get("evidence") or {}
        q = ev.get("quote", "").replace("|", "/")
        L.append(f"| {_mark(ev)} | {m.get('analyte_text')}{(' → ' + label) if label else ''} | {m.get('specimen')} | {m.get('value') if m.get('value') is not None else (m.get('value_text') or '')} | {m.get('unit') or ''} | {ref} | {m.get('interpretation')} | {m.get('timing') or ''} | “{q[:120]}” [{ev.get('anchor','')}] |")
    if rec.get("enzyme_activities"):
        L.append("")
        L.append("## Enzyme activities")
        L.append("")
        for e in rec["enzyme_activities"]:
            L.append(f"- {_mark(e.get('evidence'))} {e.get('enzyme_text')} ({e.get('tissue') or '–'}): {e.get('value')} {e.get('unit') or ''} {('%.0f%% of control' % e['percent_of_control']) if e.get('percent_of_control') is not None else ''} · {e.get('interpretation')}  \n  “{(e.get('evidence') or {}).get('quote','')}” [{(e.get('evidence') or {}).get('anchor','')}]")
    L.append("")
    L.append("## Phenotypes")
    L.append("")
    for ph in rec.get("phenotypes", []):
        ev = ph.get("evidence") or {}
        L.append(f"- {_mark(ev)} {'NOT ' if ph.get('excluded') else ''}{ph.get('text')} → {ph.get('hpo_label') or '–'} {ph.get('hpo_id') or ''} ({(ph.get('grounding') or {}).get('status')})  \n  “{ev.get('quote','')}” [{ev.get('anchor','')}]")
    if rec.get("treatments"):
        L.append("")
        L.append("## Treatments")
        L.append("")
        for t in rec["treatments"]:
            ev = t.get("evidence") or {}
            L.append(f"- {_mark(ev)} {t.get('type')}: {t.get('agent') or t.get('text')} · response {t.get('response') or '–'}  \n  “{ev.get('quote','')}” [{ev.get('anchor','')}]")
    if rec.get("outcome", {}) and (rec.get("outcome") or {}).get("text"):
        L.append("")
        L.append(f"## Outcome\n\n{rec['outcome']['text']}")
    L.append("")
    L.append(f"## Model summary (not source text)\n\n{rec.get('clinical_summary','')}")
    if rec.get("extractor_notes"):
        L.append("")
        L.append("## Extractor notes\n")
        for n in rec["extractor_notes"]:
            L.append(f"- {n}")
    path.write_text("\n".join(L))
