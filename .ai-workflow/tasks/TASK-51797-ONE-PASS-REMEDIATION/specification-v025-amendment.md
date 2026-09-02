# Remediation v025: post-R016 delta and Ruff recapture

Close the mechanical gap after FINAL-008/R-016 implementation grew the Python
delta beyond the v024 capture (111/76/35 → 124/89/35) and introduced unmapped
R-016 production/test/control paths.

## Exact cardinalities after this amendment

- complete delta Python paths: 124
- present: 89
- deleted: 35
- active: 5 (unchanged membership, zero diagnostics)
- frozen current: 84
- base-untouched: 163
- base diagnostics: 189
- frozen diagnostics: 102
- total baseline diagnostics: 291

Capture a new immutable Ruff baseline at
`.ai-workflow/tasks/TASK-51797-ONE-PASS/ruff-baseline-v025.json`. Keep
`ruff-baseline-v020.json` byte-immutable as historical evidence.

## Mapping

Add exact clean-at-capture mappings for the twelve R-016 code/test paths and
post-capture control paths for R-016 workflow artifacts plus this amendment.
