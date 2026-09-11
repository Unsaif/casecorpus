# Baseline audit — RecoX (Combined Human Reconstructions), 10 Sept 2026

File: `~/Downloads/Agentic_LLM/data/recon3d.mat` (modelID RecoX, 22,417 reactions, 13,637 metabolites, 3,402 genes as HGNC symbols; formulas and charges present; no reaction/metabolite annotation fields). A second file, `recon3d__backup_2026-05-07.mat` ("Recon - Human metabolism", 13,543 reactions, Entrez gene ids, no charges), shares 11,878 reaction ids and carries the same wrong CoA formula.

## Summary
```
{
 "model": "RecoX",
 "name": "RecoX (Combined Human Reconstructions)",
 "reactions": 22417,
 "metabolites": 13637,
 "genes": 3402,
 "exchange_like": 2452,
 "transport": 9099,
 "internal": 10866,
 "with_gpr": 15496,
 "reversible": 12258,
 "subsystems": 139,
 "annotation_fields": [],
 "internal_or_transport": 19965,
 "no_gpr": 4887,
 "mass_balanced": 14778,
 "mass_unbalanced": 2781,
 "mass_unbalanced_only_H": 222,
 "mass_generic": 2406,
 "charge_unbalanced": 1020,
 "dead_end_metabolites": 655,
 "orphan_metabolites": 0,
 "reactions_blocked_by_dead_end": 616,
 "has_reference_annotation": null,
 "has_ec_annotation": null,
 "iem_scope_genes_in_model": 658,
 "iem_reactions": 8198,
 "iem_reactions_by_gene": 5333,
 "iem_reactions_unbalanced": 1925,
 "iem_reactions_blocked_by_dead_end": 282,
 "iem_reactions_no_gpr": 239
}
```

## Finding 1 — three cofactor formulas are wrong
Free coenzyme A carries the formula of a C5 enoyl-CoA (C26H38N7O17P3S, identical to tiglyl-CoA / 3-methylcrotonyl-CoA) instead of C21H32N7O16P3S; L-carnitine carries the formula of 5-hydroxyhexanoylcarnitine (C13H25NO5) instead of C7H15NO3. Other cofactors checked (ATP, ADP, AMP, NAD(P)(H), FAD(H2), acetyl-CoA, PPi, Pi, glutamate, glutamine, water, CO2, O2) are correct.

Mass-unbalanced internal/transport reactions: **2781 → 1037** after correcting CoA → **547** after correcting CoA and carnitine. Of the remaining 547: 145 share the pattern {O:3, S:1} and all involve PAPS (formula `C10H11N5O10P2` = PAP; should be `C10H11N5O13P2S`), ~265 are single- or double-proton imbalances (typical charge-state bookkeeping), the rest are individual reactions listed in `unbalanced_after_cofactor_fix.csv`.

How it was found: group unbalanced reactions by their imbalance vector, then count which metabolites all reactions in a group share. Three groups, three ubiquitous metabolites. This is the kind of check that must run before any literature-derived reaction is judged "unbalanced" — otherwise the claim would be blamed for the model's bookkeeping.

## Finding 2 — the IEM slice
658 of the 1,225 IEM-scope genes (casecorpus scope, MONDO:0019052 subtree) occur in the model; 8198 reactions are IEM-related (5333 through a scope gene, the rest through the "IMD" subsystems). 282 IEM reactions touch a dead-end metabolite; 239 IMD-subsystem reactions have no GPR. `iem_genes.txt` (658 symbols) is the input for the UniProt gold set and for reaction-centred literature retrieval.

## Files
- `reactions.csv/parquet` — per-reaction audit (kind, subsystem, GPR, genes, IEM flags and diseases, mass/charge status, imbalance vector, dead-end involvement, equation)
- `dead_end_metabolites.csv`, `metabolites.csv`
- `unbalanced_after_cofactor_fix.csv`, `cofactor_formula_finding.json`
- `iem_genes.txt`

## Caveats
- "Blocked by dead end" is topological (necessary, not sufficient); FVA-based blocked reactions and energy-generating-cycle tests need cobrapy + a solver and run on the user's machine.
- Generic-group formulas (R, X, FULLR) are skipped, not judged.
- Whether RecoX is the model the lab actually uses, and where its formulas came from, is for Tim to say; the finding is about this file.
