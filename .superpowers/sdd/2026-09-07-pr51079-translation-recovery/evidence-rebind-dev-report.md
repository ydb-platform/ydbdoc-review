# PR51079 coverage evidence rebind developer report

Date: 2026-09-08

## Scope and identity

- Base: `d25fa14b00a63d2862e757ad365011ae96371398`.
- Branch: `fix/pr51079-evidence-rebind`.
- Worktree: `/Users/iuriisintiaev/ydbdoc-review/.worktrees/pr51079-evidence-rebind-dev`.
- Analyst contract: `/private/tmp/pr51079-verify-evidence-analyst-report.md`.
- No YDB, GitHub, PR, tag, release, label or production workflow mutation was performed.

## Root cause and RED

The ordinary coverage loader correctly looks up only the exact candidate SHA and
bound digest. A manually repaired direct child therefore cannot load the root
candidate's evidence, even when its only byte delta is the deterministic
same-fragment repair already used by the verifier.

The new focused suite was added before production code. Collection failed because
`ydbdoc_review.ops.coverage_rebind` did not exist, establishing RED before the
new operation was implemented. Existing coverage, href and workflow baseline tests
were green before the change.

## Implementation

`src/ydbdoc_review/ops/coverage_rebind.py` adds a separate metadata-only operation.
It does not modify `load_coverage_evidence`, `CoveragePlan`, verification admission,
candidate files or Git history.

The deterministic derivation path:

- requires exact C and K commits, with K having exactly one parent and that parent C;
- reconstructs the trusted C evidence from frozen source R, baseline B and candidate C
  bytes, checking source, baseline and candidate hashes plus the complete plan path set;
- inspects the complete raw Git tree delta with status, object type and mode, rejecting
  additions, deletions, renames, non-blob objects, symlinks, submodules, executable-bit
  changes, unreadable data and paths outside the manifest;
- permits only existing EN Markdown targets whose plans are full prose
  `translate_required` plans, rejecting units and protected-only plans;
- checks the existing href contract and replays
  `reconcile_final_en_same_fragment_paths` from frozen H0/R/B/C bytes with the exact K
  reader;
- requires a non-noop byte-for-byte match of every changed file and rejects every extra
  tree change;
- rebuilds a new K-bound coverage manifest while preserving all plans, source authority,
  baseline hashes and unchanged candidate hashes.

The controller loads C only through the existing strict loader and explicit trusted
run/digest. It validates the authority envelope and current remote head, verifies the
caller-supplied expected K digest, saves K evidence immutably with read-back, then saves
a self-digesting immutable repair audit receipt. Immediately before metadata mutation it
rechecks repository, head, exact PR body and authority binding. It replaces only the
coverage fields inside the existing marker, keeping artifact root C and all RED prose.
Identical retries are idempotent; conflicts and read-back failures remain fail-closed.
A metadata failure after durable evidence can be retried without changing candidate
history.

## Threat coverage

The 21 focused tests cover the real Git four-snapshot controller path, strict loader
failure before binding, independent exact loader acceptance after binding, immutable C
retention, units-plan byte retention and mandatory semantic validation. Negative controls
cover extra prose, alternate hrefs, changed fragments and labels, deletion/config edits,
units and protected-only changes, extra paths, mode changes, non-child K, wrong binding
and expected digests, old source/baseline/candidate/authority corruption, remote head and
body drift, null store, evidence/audit conflicts, idempotent retry and retry after metadata
failure. Existing PR51079 anchor tests independently retain ambiguity, source-lineage and
paragraph-movement controls for the reused repair helper.

## Verification

- Focused evidence-rebind suite: 21/21 passed.
- Task 7 coverage/source-preserving group, including the new suite: 299/299 passed.
- Workflow/publication/checkpoint/resume group: 260/260 passed.
- Full `tests/unit` collection: 2071 tests collected successfully.
- Ruff on changed Python files: passed.
- `compileall` on changed Python files: passed.
- `git diff --cached --check`: passed.

The repository's strict mypy run still reports its existing cross-module baseline debt.
One new local mismatch from passing legacy string href issues into the typed repair helper
was removed by explicitly rejecting a non-clean contract before replay. No remaining
reported mypy item points to `coverage_rebind.py`.

The broad groups overlap, so their counts must not be summed. A complete runtime execution
of every unit test was not requested for this developer gate; full collection succeeded.

## Independent tester focus

Confirm that the operation cannot accept any edit based on small diff size or matching
fragment alone. In particular, inspect raw-tree parsing, the exact old-evidence
reconstruction, second PR snapshot ordering, immutable evidence/audit persistence and
artifact-root preservation. Re-run the focused suite and the two broad groups above from
the committed SHA. The live PR52432 recovery and expected production digest
`8a16af708cd613b4a4063720879e0303cbdb78f4dcb1cd00ad548a02ab4f4251` remain controller
actions after independent approval; this developer performed no external mutation.
