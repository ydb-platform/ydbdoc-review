# TASK-51797 one-pass remediation v006 amendment

## Scope

This amendment changes only the R-004 manifest bootstrap contract approved in
v005. All other v005 requirements, ordering, allowlists and prohibitions remain
unchanged. It closes `questions/R-004-manifest-bootstrap.md` without giving the
developer any ownership, requirement-mapping, path-selection or tool-selection
choice.

## Observed immutable facts

- `baseline-snapshot-remediation-v005.yaml` was captured once and contains
  exactly 114 entries.
- Contrary to the intended v005 prose order, that immutable snapshot records
  untracked `uv.lock` as present. The snapshot must not be overwritten or
  edited to conceal this fact.
- After capture, `uv.lock` was removed and the analyst question was added. This
  produces 114 current delta paths before v006 publication.
- The manifest is itself an untracked path in the final whole-worktree delta.
  Requiring its literal file SHA inside its own entry is self-referential and
  cannot be satisfied. The exact normalized self-hash rule below replaces only
  that impossible literal-SHA requirement.

## Required implementation

Add a `bootstrap-manifest` subcommand to `scripts.remediation_policy_gate.py`
and tests only in `tests/unit/test_remediation_policy_gate.py`. The command,
schema, mapping tables, precedence and serialization are fully defined by
`implementation-plan-v006-amendment.yaml`.

The command derives every entry. The developer must not hand-author entries,
choose ownership classes, choose requirement IDs, omit paths, add paths, or
change a mapping. An unmapped or multiply classified path is a hard RED error.
No default or best-effort classification is allowed.

The bootstrap reads the current worktree only after the reviewed v006
artifacts have been copied byte-identically into it. It includes those
post-capture protocol artifacts and the output manifest itself. It excludes
`uv.lock` only because the current delta no longer contains it, while the
separate `required_absent` gate proves its absence. The immutable snapshot
continues to record that `uv.lock` existed at capture time.

The manifest self-entry uses `kind: present` and a normalized SHA-256. To
calculate it, serialize the complete manifest using
`yaml.safe_dump(sort_keys=False, allow_unicode=True)`, but replace only the
self-entry's `final_state.sha256` with the empty string before hashing the UTF-8
bytes. Then write the resulting digest into that field and serialize once more.
Validation repeats the same normalization. No iterative fixed-point search and
no literal on-disk self-hash comparison are allowed.

## Exact execution order

1. Keep the captured v005 snapshot byte-identical.
2. Materialize the externally approved v006 specification, plan amendment,
   request and response byte-identically in the worktree.
3. Confirm `uv.lock` is absent.
4. Implement and test only the new bootstrap behavior.
5. Run tests using the existing virtual environment, never an `uv` command:
   `.venv/bin/python -m pytest tests/unit/test_remediation_policy_gate.py`.
6. Confirm `uv.lock` is still absent.
7. Run the exact `bootstrap-manifest` command from the v006 plan. It must refuse
   to overwrite an existing manifest.
8. Run the existing v005 validation command.
9. Confirm `uv.lock` is still absent and run `git diff --check`.

`uv lock`, `uv sync`, `uv run` and every other `uv` invocation are forbidden
for this remediation because they may recreate the unapproved lock file. The
existing `.venv/bin/python`, `.venv/bin/pytest` and `.venv/bin/ruff` executables
are the only approved local Python tool entry points. If the virtual environment
is missing or unusable, the developer stops R-004 and reports a question. The
developer must not regenerate dependencies.

## Acceptance

- Bootstrap covers the exact current delta plus its own output path exactly
  once.
- Every mapping is derived from one named source table and the fixed precedence.
- `baseline_state` comes only from the immutable snapshot or the exact absent
  state for declared post-capture paths.
- `final_state` matches the current worktree, using only the normalized self-hash
  exception for the manifest itself.
- `uv.lock` is absent before tests, after tests, after bootstrap and after final
  validation.
- Existing v005 validation is GREEN.
- No developer choice or silent scope expansion remains.
