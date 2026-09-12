# Aggregate B6 developer report

## Scope

- Base: `fde41db6127a538e7c5704bf703af368b06fb370`.
- Branch: `fix/pr51079-aggregate-b6`.
- Changed only `tests/unit/test_en_link_targets.py` and this report.
- Production code was not changed.

## Root cause

The aggregate file had three stale failures against the current EN link-target
contract.

Two tests passed a removed `ru_text` keyword to
`check_en_page_link_targets`. The current fail-closed contract suppresses only
an issue independently reproduced from `baseline_text` and
`baseline_read_text`, rather than treating a matching RU link as permission to
ignore a broken EN target.

The third test used `baseline_read=lambda _p: en`. That returned the referrer
page for every baseline target path. Final readers reported missing files while
the false baseline tree reported present files with missing fragments, so the
issue identities correctly differed and could not be classified as ambient.
Its second assertion also expected wrapper-parity findings from
`apply_en_link_target_checks`, although that function only produces typed
`en_link_target` findings.

## TDD repair

The unmodified test file reproduced `3 failed, 7 passed`: two `TypeError`
failures for `ru_text` and one unexpected broken path from the invalid baseline
reader.

The tests now model distinct frozen and final EN trees. Existing missing files
and fragments are suppressed only when the same target issue exists in both
trees. Each control introduces a genuinely new broken candidate link and
asserts that it remains blocking and is attached as an `en_link_target`
heuristic. Irrelevant RU wrapper data was removed from the link-target wrapper
test.

## Verification

- Exact file: `10 passed in 0.75s`.
- Adjacent link, coverage, and source-preserving suite: `234 passed in 22.68s`.
- Unit collection: `2025 tests collected in 0.88s`.
- Ruff check: passed.
- Ruff format check: passed.
- `git diff --check`: passed.

The broader exploratory run included
`tests/unit/test_source_preserving_label_contract.py` and reported its eight
known A05 fixture failures because the baseline history lacks
`ydb/docs/en/core/toc_p.yaml`. Those failures were present on the task base,
are outside B6, and did not appear in the 234-test B6 gate. No test or
production workaround was added for them.

No push, tag, GitHub mutation, release, or production action was performed.
