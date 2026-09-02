## Factual Ruff baseline correction v023

This amendment corrects only the measured baseline model in v020. The v020
capture correctly refused to write an artifact because the exact base-archive
partition contains 198 diagnostics, not 306.

### Independently reproduced facts

Using Ruff 0.16.5, base
`9ff8edec9a26d3975306e20adca325c6eb9f77e6`, the approved arguments
`check --no-cache --output-format=json`, and repository-root working directories:

- base contains 265 Python paths;
- the current whole delta contains 110 Python paths: 56 modified and present,
  19 absent at base and present now, and 35 deleted;
- excluding all 110 delta paths leaves 174 base-untouched Python paths;
- Ruff on those 174 paths in the base archive returns exactly 198 diagnostics;
- current repository-wide Ruff returns 306 diagnostics;
- the same 174 unchanged base paths account for 198 current diagnostics;
- historical changed-present paths account for the remaining 108: 100 on
  base-present modified files and 8 on base-absent new files;
- the 8 new-file diagnostics are exactly 6 in
  `tests/unit/test_atom_round_trip.py`, 1 in
  `tests/unit/test_translation_dependency_queue.py`, and 1 in
  `tests/unit/test_translation_provenance.py`;
- the five Ruff-remediation active paths listed below have zero diagnostics;
- base and current `pyproject.toml` SHA-256 are both
  `822a7d659b893cc498725c18df0c72060f2eeba2df89725c520d4f5ed492ec29`.

Therefore the 108 difference is not a Ruff version, configuration or working
directory difference. It is diagnostics in historical approved/frozen delta
files that this narrow Ruff remediation is forbidden to edit.

### Exact three-part policy

Replace v020 schema version 1 with schema version 2 and partition Python paths:

1. `active_python_paths`, exact five paths:
   `src/ydbdoc_review/pipeline/navigation_merge.py`,
   `scripts/remediation_ruff_gate.py`,
   `tests/unit/test_remediation_ruff_gate.py`,
   `scripts/remediation_policy_gate.py`, and
   `tests/unit/test_remediation_policy_gate.py`. Every present active path must
   remain Ruff-clean. This preserves the changed-files-zero requirement for all
   files changed by the Ruff remediation.
2. `base_untouched_paths`: exact 174 base-present paths outside the complete
   delta. Capture their 198 diagnostics from the base archive. Validation lints
   the same current paths and requires a diagnostic multiset subset of the
   immutable 198-record partition.
3. `frozen_current_paths`: exact 70 present historical delta paths outside the
   active set. This includes base-present modified and base-absent new files.
   Capture each current SHA-256 and the exact 108 diagnostics. Validation
   requires identical path set and bytes, then requires a diagnostic multiset
   subset of the immutable 108-record partition. These files are not a waiver:
   any byte change is RED before lint comparison.

Record the 35 deleted Python paths separately and require them to remain absent.
Any new, removed, reclassified or renamed Python path is RED. Each diagnostic
uses the v020 exact seven-field record. A new/moved/changed diagnostic or
duplicate-count increase in either baseline partition is RED. Removal is
allowed. Total current diagnostics may not exceed 306, but count-only comparison
remains forbidden.

Enumerate repository Python paths only from `git ls-tree` for base and the union
of tracked-present plus untracked-not-ignored `git ls-files` output for current.
Never use recursive filesystem discovery. This excludes `.venv`, caches and
ignored dependencies even when explicit Ruff paths would bypass exclusions.

Resolve `.venv/bin/ruff` once with `Path(...).resolve(strict=True)` while cwd is
the real worktree. Use that absolute executable for both archive and current
subprocesses. Artifact metadata remains the approved relative command
`.venv/bin/ruff check --no-cache --output-format=json`. Both runs use their
respective repository root as cwd, and capture requires the equal config hash
above and Ruff version `ruff 0.16.5`.

The v021 mapping and capture-once lifecycle remain unchanged. v023 resolves
exact predecessor v022, self-seals its own four protocol files plus
`response-v022.yaml`, then requires focused tests, full pytest, capture once,
Ruff validate, manifest refresh and immediate policy validate.

