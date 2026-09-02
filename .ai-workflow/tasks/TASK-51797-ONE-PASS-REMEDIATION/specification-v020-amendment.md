## Fail-closed Ruff baseline v020

This amendment changes only the impossible v019 requirement that the complete
repository become Ruff-clean. The v019 target is otherwise complete: the three
named navigation diagnostics are gone, exact-file Ruff is GREEN, focused tests
are 36 GREEN and full pytest is 1354 GREEN. Current repository-wide Ruff reports
306 unrelated pre-existing diagnostics. Fixing or suppressing them is outside
this task.

### Exact policy

Add `scripts/remediation_ruff_gate.py` and
`tests/unit/test_remediation_ruff_gate.py`. The gate uses only
`.venv/bin/ruff` with `check --no-cache --output-format=json`. It has two
commands: `capture-baseline` and `validate`.

Capture occurs once at the final quiescent checkpoint after every approved
Python edit and after the externally approved v020 protocol files are present.
It refuses an existing output. Its immutable source is base commit
`9ff8edec9a26d3975306e20adca325c6eb9f77e6`, materialized read-only into a
temporary directory with `git archive`; it does not switch branches or mutate
the worktree.

The generated artifact is
`.ai-workflow/tasks/TASK-51797-ONE-PASS/ruff-baseline-v020.json`. It contains
exactly:

```text
schema_version: 1
base_commit: exact 40-character SHA
ruff_version: exact stdout of `.venv/bin/ruff --version`
command: [".venv/bin/ruff", "check", "--no-cache", "--output-format=json"]
changed_python_paths: sorted unique present-or-deleted *.py delta paths
untouched_diagnostic_count: 306
untouched_diagnostics: sorted diagnostic records
```

Each diagnostic record contains exactly `path`, `row`, `column`, `end_row`,
`end_column`, `code`, and `message`. Paths are repository-relative POSIX paths.
Records are sorted by those seven fields and duplicates are retained. JSON is
UTF-8, `ensure_ascii=False`, `sort_keys=True`, indent 2, final newline.

`changed_python_paths` is derived from the complete tracked and untracked delta
against the base. Every present changed Python file is excluded from the
baseline set and must independently produce zero diagnostics. Deleted Python
files are recorded but not linted. The baseline Ruff run covers only base
Python paths not in `changed_python_paths`; its expected count is exactly 306.
Any other count at capture is RED and no artifact is written.

Validation is fail-closed:

1. verify artifact schema, exact base, exact Ruff version and capture command;
2. recompute the complete changed/deleted Python path set and require exact
   equality with `changed_python_paths`;
3. require every present changed Python file, including both gate script and
   test, to have zero Ruff diagnostics;
4. run Ruff on the current untouched Python paths;
5. normalize and sort diagnostics by the exact record schema;
6. require the current diagnostic multiset to be a subset of the immutable
   306-record baseline multiset and its cardinality to be at most 306.

A new record, increased duplicate count, moved diagnostic, changed code/message,
new changed Python path or any diagnostic in a changed Python file is RED.
Removal of a baseline diagnostic is allowed. No `noqa`, per-file ignore, config
change, count-only comparison, blanket waiver or unrelated formatting is
authorized.

Run Ruff validation after full pytest and before v020 manifest refresh. Then run
v020 refresh-manifest and immediate policy validate with no intervening write.

v020 resolves exact predecessor v019, self-seals its own four protocol files,
and explicitly authorizes only the new Ruff gate script, its tests and the
generated baseline artifact.

