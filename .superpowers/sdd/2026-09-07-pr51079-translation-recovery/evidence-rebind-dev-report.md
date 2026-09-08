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
caller-supplied expected K digest, saves K evidence with read-back, then saves the exact
repair audit. After a final read-only head/authority check it persists a canonical
attestation last, addressed by repository, source PR, translation PR, C, old run and
digest, K, and repair rule. It never updates the PR body. Identical retries produce the
same bytes; conflicts, partial objects and read-back failures remain fail-closed.

The workflow adapter activates only when the ordinary envelope remains rooted at C while
the exact checkout is K. It derives the one attestation key without scans, strictly loads
C, repeats the complete deterministic proof, compares the exact audit and digests, and
finally loads K through the unchanged strict coverage loader. Receipt-backed verification
is read-only for candidate and PR body: it runs fresh QA and all final gates on K, but a
critic-proposed K2 becomes an explicit checkout mismatch/RED instead of a push or body
rewrite. Ordinary exact-envelope verification remains on its prior path.

## Threat coverage

The 34 focused tests cover the real Git four-snapshot controller path, strict loader
failure before binding, independent exact loader acceptance after binding, immutable C
retention, units-plan byte retention and mandatory semantic validation. Negative controls
cover extra prose, alternate hrefs, changed fragments and labels, deletion/config edits,
units and protected-only changes, extra paths, mode changes, non-child K, wrong binding
and expected digests, old source/baseline/candidate/authority corruption, remote head and
marker drift, null store, evidence/audit/attestation conflicts, idempotent retry and retry
after attestation failure. Complete-tuple mismatches, partial/unknown/unsupported receipts
and valid-self-digest forged proof data reject. The exact old PATCH-window race proves zero
body-update calls and preservation of the concurrent human prose edit. Existing PR51079
anchor tests independently retain ambiguity, source-lineage and
paragraph-movement controls for the reused repair helper.

## Verification

- Focused evidence-rebind plus receipt-backed/ordinary workflow tests: 36/36 passed.
- Expanded proof/coverage/source-preserving/href group: 192/192 passed.
- Workflow/checkpoint/resume group, including publication paths: 99/99 passed.
- Full `tests/unit` collection: 2085 tests collected successfully.
- Ruff on changed Python files: passed.
- `compileall` on changed Python files: passed.
- `git diff --check`: passed.

The repository's strict mypy run still reports its existing cross-module baseline debt.
One new local mismatch from passing legacy string href issues into the typed repair helper
was removed by explicitly rejecting a non-clean contract before replay. No remaining
reported mypy item points to `coverage_rebind.py`.

The broad groups overlap, so their counts must not be summed. A complete runtime execution
of every unit test was not requested for this developer gate; full collection succeeded.

## Independent tester focus

The first independent pass rejected commit `e12fd24` because GitHub does not provide a
documented compare-and-set precondition for pull-request body PATCH. This follow-up uses
the analyst's Recommendation D: trusted-store exact attestation with no GitHub body write.

Confirm that the operation cannot accept any edit based on small diff size or matching
fragment alone. In particular, inspect raw-tree parsing, the exact old-evidence
reconstruction, second PR snapshot ordering, attestation-last persistence, proof replay,
strict K loading, body preservation and artifact-root preservation. Re-run the focused
suite and the two broad groups above from
the committed SHA. The live PR52432 recovery and expected production digest
`8a16af708cd613b4a4063720879e0303cbdb78f4dcb1cd00ad548a02ab4f4251` remain controller
actions after independent approval; this developer performed no external mutation.
