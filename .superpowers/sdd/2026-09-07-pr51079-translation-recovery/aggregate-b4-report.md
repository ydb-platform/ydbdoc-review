# Aggregate B4 report: persisted `doc_continue` admission

Date: 2026-09-07

Status: DONE

Base: `6be3d9ab572caeb34bbc42d7e7961667b4aadd68`

## Root cause

`_load_continuability_for_continue` and `_persist_continuability` existed, but
the dispatcher never called either helper. Consequently, a translation receipt
or simply reaching `run_doc_continue` was enough to dispatch translation or
verification without an explicit saved unfinished stage.

## Delivered behavior

- `run_doc_continue` now runs the existing ACL, quota, parent-context, TTL, and
  continue-count gate before dispatch.
- Dispatch requires an explicit saved continuability state with a non-empty
  unfinished stage and fixed SHAs. Source PR and, when saved, translation PR
  identities must match the current request.
- A local state artifact is authoritative, including an explicit terminal deny.
  Only a missing local artifact falls back to the trusted parent-run store, so a
  fresh runner can resume without allowing stale parent state to override a
  newer local terminal decision.
- Missing, denied, expired, and identity-mismatched state returns a blocked
  `DocJobResult` before translation or verification. Translation receipts and
  checkpoints never grant admission by themselves.
- Direct `run_doc_translate(..., ops_mode="continue")` and
  `run_doc_verify(..., ops_mode="continue")` calls repeat the saved-state check,
  preventing an API-level dispatcher bypass.
- One pre-authorized ops context is passed from the dispatcher to its child, so
  ACL, quota, continue accounting, transcript parentage, and lifecycle finishing
  remain a single job rather than being opened twice.
- A new translation attempt starts with continuability cleared after authority
  SHA freeze. Once a concrete translation PR has been pushed, the workflow
  persists `unfinished_stage="verify"` locally and in the trusted current-run
  store before inline verification begins.
- Clean or otherwise terminal translation/verification results clear admission.
  A non-`PUBLISH_RED` result with remaining verification blockers keeps the
  stage continuable. A verification exception after publication leaves the
  already-persisted unfinished stage available for a later trusted continue.

No change was needed in `ops/job_state.py`. `test_ops_lifecycle.py` remained a
read-only control.

## TDD evidence

Baseline exact command:

```text
YDBDOC_RUNS_LEDGER=memory YDBDOC_TRANSCRIPT_BACKEND=memory \
  /Users/iuriisintiaev/ydbdoc-review/.venv/bin/python -m pytest \
  tests/unit/test_github_workflow.py tests/unit/test_job_state.py \
  tests/unit/test_ops_lifecycle.py tests/unit/test_translation_resume.py -v
```

Initial RED: 76 collected, 75 passed, 1 failed. The existing
`test_run_doc_continue_refuses_without_continuability_flag` demonstrated that
translation was dispatched without admission.

Additional controls were first observed failing for:

- trusted-store continuation from a fresh checkout;
- explicit local denial and wrong translation identity overriding an older
  trusted allow;
- ACL denial before dispatch;
- a valid translation receipt without continuability state;
- direct continue-mode API dispatch without saved admission;
- persistence before inline verification and terminal clear/update behavior.

Final exact command: 84 collected, 84 passed in 17.11 seconds.

Static verification:

```text
/Users/iuriisintiaev/ydbdoc-review/.venv/bin/python -m ruff check --ignore RUF001 \
  src/ydbdoc_review/github/workflow.py \
  tests/unit/test_github_workflow.py tests/unit/test_translation_resume.py
git diff --check
```

Result: all checks passed. `RUF001` is excluded for the repository's existing
Cyrillic test and message text.

## Independent-review repair

The first B4 commit, `4d8458e1c86cb53d2f34256f73705ecce7cc2b5c`,
did not propagate its admitted ops context through the two recursive inline
`run_doc_verify` returns. The outer call returned before lifecycle completion,
while the terminal recursive call had `ops_ctx=None`; therefore no terminal
ledger/transcript record was written.

Focused TDD reproduced both branches. The ordinary recursive branch performed
one inline publication, and the depth-limit branch performed three publications
before the final read-only verification. Before the repair both assertions
failed with `finish_ops_job` called zero times. The implementation now passes
the same identity-validated context through both recursive calls. An external
`ops_mode="continue"` call cannot use `skip_ops_gates=True` to impersonate an
internal recursive verification, with or without an injected context.

Focused final result: 4 passed. The tests assert exactly one dispatcher
`begin_ops_job`, exactly one real `finish_ops_job`, one terminal in-memory ledger
record, a terminal transcript manifest, and matching continuability outcomes:
clean final verification clears admission; a final blocker retains it.

Final review-repair gates, all with `PYTHONPATH=src`, memory persistence
backends, and `XDG_CACHE_HOME` under `/private/tmp`:

- mandatory workflow/job-state/lifecycle/resume gate: 88 passed in 21.46s;
- checkpoint/source-preserving/resume gate: 90 passed in 12.30s;
- recursive frozen-wrapper/fixup contract: 30 passed in 185.43s;
- Ruff with the documented `RUF001` exception and `git diff --check`: passed.

## Scope and exclusions

Changed product code only in `src/ydbdoc_review/github/workflow.py`; tests only
in `tests/unit/test_github_workflow.py` and
`tests/unit/test_translation_resume.py`. No real YDB, production, GitHub
mutation, push, or tag operation was used.
