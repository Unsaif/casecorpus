# VMH ReconX (JSON download, 10 Sept 2026) vs the lab's combined RecoX .mat

## VMH ReconX (`reconX.json`, id "reconX", version 1)
14,160 reactions · 8,876 metabolites · 3,299 genes (HGNC symbols) · 9 compartments · no annotation fields in the JSON export.

Audit: 12093 internal/transport reactions; 4351 without GPR; **183 mass-unbalanced** (123 proton-only), 1844 with generic groups (not judged); 529 charge-unbalanced; 465 dead-end metabolites; 394 reactions topologically blocked. Cofactor formulas (CoA, carnitine, PAPS, ATP, NAD(P)(H), acetyl-CoA) are all correct.

IEM slice: 657 of 1,225 scope genes present; 3967 IEM-related reactions (3813 via a scope gene); 74 unbalanced; 150 touch a dead end.

## Relationship to the lab's `recon3d.mat` ("RecoX (Combined Human Reconstructions)", 22,417 reactions)
- 11,841 reaction ids shared; 2,319 only in VMH ReconX (mostly extracellular/ER/mitochondrial transport and drug metabolism); 10,576 only in the lab .mat — 2,608 "Metabolism, IMD reactions", 1,108 "Transport, IMD reactions", plus large transport and PUFA blocks (the Recon4IMD-type additions).
- Only 252 of the lab .mat's 3,986 IMD-subsystem reactions exist in VMH ReconX.
- The three corrupted cofactor formulas (CoA, L-carnitine, PAPS) are **absent from VMH ReconX and present in the lab .mat** (and in the "Recon" backup next to it): the corruption was introduced downstream of VMH, in whatever produced those files. Fix in the .mat: coa → C21H32N7O16P3S, crn → C7H15NO3, paps → C10H11N5O13P2S.

## Consequence for the evidence layer
Key everything on the VMH reaction id. VMH ReconX is the clean public baseline; the lab's combined .mat contributes the IMD reactions (after the formula fix); Harvey/Harvetta consume the same ids per organ. Existing references, EC numbers and cross-references are not in the JSON export but are available per reaction from the VMH web API (`/_api/reactions/`), which the evidence baseline should pull on the user's machine.
