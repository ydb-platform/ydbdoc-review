# PR51079 paragraph-local historical EN path reuse

Date: 2026-09-08

## Scope and identity

- Base: `a3f51b2d9990f993bb1945619ec2f15d8a7e681a`
- Branch: `fix/pr51079-anchor-paragraph-reuse`
- Worktree: `/Users/iuriisintiaev/ydbdoc-review/.worktrees/pr51079-anchor-paragraph-reuse`
- Analyst contract: `/private/tmp/pr51079-anchor-qa-analyst-report.md`
- Production scope: `src/ydbdoc_review/validation/href_parity.py`
- No GitHub, PR52432, label, release, tag or production workflow mutation was performed.

## Root cause and TDD evidence

The reproduced four-snapshot topology was R0/R1/E0/E1 = 70/75/67/75 internal
links. The target `security-auth` occurrence was unique and unchanged in RU, and
the frozen/candidate EN paragraph differed only in the href path. The existing
document-wide `len(ru_base_links) == len(en_tip_links)` precondition returned the
candidate unchanged before evaluating that local proof.

RED command:

```sh
env XDG_CACHE_HOME=/private/tmp/ydbdoc-anchor-reuse-cache \
  YDBDOC_RUNS_LEDGER=memory YDBDOC_TRANSCRIPT_BACKEND=memory PYTHONPATH=src \
  /Users/iuriisintiaev/ydbdoc-review/.venv/bin/python -m pytest \
  tests/unit/test_pr_51079_anchor_reuse.py -q
```

Before production changes: 3 expected failures and 2 passing negative controls.
The failing cases were exact 70/75/67/75 topology, unrelated internal-link edits
around the target paragraph, and destination wrapper/title preservation.

## Implementation

The existing positional proof remains active when all four snapshots align.
When document-wide cardinalities differ, the new fallback requires:

- a unique unchanged normalized-label plus decoded-full-href RU occurrence in R0/R1;
- unique identical decoded ASCII fragment lineage across the four snapshots;
- a unique candidate href equal to the current source href;
- unique E0/E1 paragraph correspondence after masking only that href path;
- identical target paragraph ordinals in R0/R1 and E0/E1;
- broken immutable RU and candidate EN targets;
- a resolvable frozen EN target inside the same `docs_root/en/core` boundary;
- no existing deterministic link-contract issue.

The writer replaces only the candidate path slice. It preserves the raw fragment,
label, angle wrapper, optional title, whitespace and all surrounding bytes. Missing
or ambiguous evidence remains a no-op, so the existing final link gate stays
fail-closed.

The production workflow regression now uses 70/75/67/75 internal links and distinct
source-base, source-current and tip-EN refs. The focused PR51079 tests also cover
paragraph-context drift, duplicate occurrences/fragments, changed RU lineage,
source-valid and candidate-valid targets, missing/deleted/fragmentless historical
targets, contract issues, raw fragment spelling and docs-root escape.

### Independent tester fix loop

The first independent test rejected commit `6c1f6d3` because a unique paragraph could
move to a different ordinal in RU or EN and still satisfy the content skeleton. Two RED
regressions reproduced R0/R1 ordinal 0 to 1 and E0/E1 ordinal 0 to 2; both initially
repaired. The follow-up requires paragraph ordinal equality in both the aligned positional
route and the unequal-cardinality fallback. The two regressions now remain unchanged,
while the 70/75/67/75 workflow and link additions/deletions inside stable surrounding
paragraphs still repair exactly one path.

## Verification

- Focused href/reuse suite: 105/105 passed after the tester fix loop.
- Exact real post-apply workflow regression: 1/1 passed.
- Task 6 coverage/provenance gate: 164/164 passed.
- Task 7 differential/source-preserving gate: 278/278 passed.
- Link/publication/checkpoint aggregate: 343/343 passed after the tester fix loop.
- Full `tests/unit` collection: passed, including 8 new PR51079 tests.
- Ruff on changed Python files: passed.
- `compileall` on changed Python files: passed.
- `git diff --check`: passed.

The aggregate commands overlap; their counts are reported independently and must
not be summed. A full runtime `tests/unit` execution was not requested for this
developer gate; collection of the complete suite succeeded.

## Independent tester focus

Verify that the fallback cannot repair by fragment uniqueness alone. In particular,
mutating EN paragraph text, duplicating either occurrence, changing the RU label or
href, making either current target valid, removing the historical target, or moving
it outside the docs root must leave the candidate unchanged and therefore visible to
the final link gate.
