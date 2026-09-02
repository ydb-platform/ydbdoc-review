# Implementation report: one-pass-v010 remediation

Status: **NOT READY**. The developer implementation and its local verification
are complete. Independent tester verification and the configured external
implementation review are still mandatory before this status may become READY.

## Whole-delta inventory

The derived remediation manifest contains exactly 187 paths against base
`9ff8edec9a26d3975306e20adca325c6eb9f77e6`: 55 modified, 43 deleted and 89
untracked. Ownership totals are 56 production, 58 test, 2 configuration, 1
script, 3 implementation-report and 67 control-artifact paths. Every manifest
path is unique and mapped by the policy gate.

All R-001 through R-021 occur in the current derived manifest. The exact
per-requirement path counts are: R-001 12, R-002 6, R-003 2, R-004 5, R-005 6,
R-006 7, R-007 1, R-008 2, R-009 6, R-010 4, R-011 6, R-012 5, R-013 2,
R-014 5, R-015 10, R-016 5, R-017 5, R-018 4, R-019 4, R-020 7 and R-021 7.

## v016 completion

`tests/unit/test_chunker.py` no longer uses the size-independent
`batch_count < 100` threshold. For every non-final batch of every non-ambiguous
real fixture it now proves the actual greedy boundary: a dense singleton, an
oversized singleton, a dense next segment, or an otherwise overflowing next
segment. The exact v014 negative-fixture behavior remains unchanged.

`scripts/remediation_policy_gate.py` resolves v016 only through exact v015
predecessor resolution and appends the four reviewed v016 protocol paths. Its
unit test rejects an incorrect v016 predecessor and proves the four-path
self-seal.

## v017/v018 completion evidence

R-009 / FINAL-010B cites these exact GREEN symbols:

- `tests/unit/test_atom_round_trip.py::test_source_owned_atoms_round_trip_without_exposure_to_model`
  proves `NOTE_TITLE` / `CUT_TITLE` round-trip without model exposure of
  protected atoms;
- `tests/unit/test_translation_transaction.py::test_unknown_segment_kind_blocks_before_render_and_stages_nothing`
  proves `UnknownSegmentKindError` as the wrapped cause, `staged == {}`,
  `accepted_payload_count == 1` and `render_count == 0`;
- `tests/unit/test_reinsert.py::test_unknown_segment_kind_is_typed_and_fail_closed`
  remains GREEN for the legacy reinsert helper.

No production change was made solely to rewrite this report.

The required fence read-only cases now live only in
`tests/unit/test_fence_comments.py`; the temporary alternate path was removed
after the combined original-path suite passed. Navigation label acquisition
uses the manifest translate pair through the bounded acquisition controller.
Complete-document validation receives one frozen context from base render
through local repair and transaction staging.

## Developer verification

- `.venv/bin/python -m pytest tests/unit/test_chunker.py tests/unit/test_remediation_policy_gate.py -q`: **36 passed**.
- `.venv/bin/python -m ruff check tests/unit/test_chunker.py scripts/remediation_policy_gate.py tests/unit/test_remediation_policy_gate.py`: **GREEN**.
- `.venv/bin/python -m pytest -q` with an isolated writable `XDG_CACHE_HOME`: **GREEN**, 1,352 tests collected.
- `git diff --check`: **GREEN**.
- v016 `refresh-manifest` was executed twice with byte-identical output.
- v016 `validate`: **GREEN**.

No commit, push, tag movement, publication or translation rerun was performed.

## Remaining independent gates

- Analyst diff-conformance audit against the approved plans and the derived
  manifest.
- Independent tester policy and functional verification.
- Configured external implementation review.
- Final approved Ruff gate required by the remediation specification. No
  developer waiver is claimed.

## FINAL-001 through FINAL-010 execution evidence

- FINAL-001: the combined original-path fence suite is present and GREEN;
  `test_fence_comments_read_only.py` is deleted after its five unique cases
  moved into `test_fence_comments.py`.
- FINAL-002 and FINAL-003: real `run_doc_translate` sentinel tests prove TOC
  reachability forwarding and root-locale page queueing.
- FINAL-004: `test_placeholder_repair.py` again proves protect-marker blocking.
- FINAL-005: navigation uses the manifest translate pair via bounded
  acquisition, with `navigation` metadata only and no seventh model slug.
- FINAL-006 through FINAL-008: the frozen validation context is constructed
  once and used by base render and repair; repair candidates are validated
  before insertion; critic payloads contain only block records.
- FINAL-009: the dead translation skip module and its test suite are deleted.
- FINAL-010: closed by the exact GREEN symbols listed under R-009 / FINAL-010B
  above. Report text matches those tests.

Focused v017/v018 verification: **128 passed**. Full suite with writable
`XDG_CACHE_HOME`: **1,354 passed**. `git diff --check`: **GREEN**.

The v018 manifest was refreshed twice after this report and the two output
hashes were byte-identical; immediate validation was **GREEN**.

Ruff remains RED only for pre-existing ambiguous Unicode comments in
`navigation_merge.py` lines 159, 161 and 203 (RUF002/RUF003). No waiver or
unapproved comment rewrite is claimed. Status stays **NOT READY** pending
analyst audit, independent tester, and external implementation review.

## v019 mechanical closure

The three approved ASCII-only prose substitutions in `navigation_merge.py`
remove the named Ruff diagnostics without changing runtime code. The policy
gate resolves v019 through v018 and authorizes only the two exact clean-at-
capture R-021 deletions after proving present base and deleted final state.
The exact-file Ruff check is GREEN, gate units are GREEN, full pytest remains
GREEN, and the v019 manifest refresh/validate cycle is GREEN. Status remains
**NOT READY** pending independent audit, tester and external review.


## v025 / FINAL008-IMPL-007 closure

User-authorized mechanical and semantic closure on 2026-09-02:

- Attempt-local complete-document validation context before freeze (FINAL008-IMPL-007).
- Remediation amendment `one-pass-remediation-v025` with Ruff baseline
  `ruff-baseline-v025.json` (124/89/35, 5/84/163, diagnostics 189/102/291).
- Manifest refresh ×2 byte-identical; policy and Ruff validate GREEN.
- Expanded R-016 isolated mutation matrix in `test_complete_document_validation.py`.
- Package ready for independent second-model review: see `READY-FOR-EXTERNAL-REVIEW.md` + `review-request-v003.md` (no self-APPROVED response).
