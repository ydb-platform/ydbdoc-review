# READY FOR EXTERNAL REVIEW (second model)

Status: **implementation complete, awaiting independent review**.
No in-process reviewer is running. A second neural network should review this
package and write `response-v003.yaml`.

## Worktree

- Path: `/private/tmp/ydbdoc-review-one-pass-v003`
- Branch: `agent/task-51797-one-pass-v003`
- Implementation tip: `37b9504` (evidence + handoff package). Review branch tip may include later docs-only commits.
- Tags: `one-pass-v025-closure`, `one-pass-v003-impl007-ruff-tests`
- Base: `9ff8edec9a26d3975306e20adca325c6eb9f77e6`
- Canonical workflow copies: `/Users/iuriisintiaev/ydbdoc-review/.ai-workflow/tasks/`

## Claimed closed

| ID | Claim |
|----|-------|
| FINAL008-IMPL-007 | Attempt-local complete-document context; freeze only after accept |
| FINAL008-IMPL-001..004,006 | Previously CLOSED in v002 |
| FINAL008-IMPL-005 | Named v001–v005 evidence categories covered with isolated GREEN/RED proofs |
| Remediation v025 | Ruff + policy gates GREEN; manifest refresh ×2 byte-identical |
| §6.239 | Memory Bank decision recorded |

## Exact review entrypoint

Read and answer:

`tasks/TASK-51797-R016-COMPLETE-DOCUMENT-VALIDATION-IMPLEMENTATION-AUDIT/review-request-v003.md`

Write:

`tasks/TASK-51797-R016-COMPLETE-DOCUMENT-VALIDATION-IMPLEMENTATION-AUDIT/response-v003.yaml`

under the canonical tree
`/Users/iuriisintiaev/ydbdoc-review/.ai-workflow/`.

## Verification commands (must be GREEN)

```bash
cd /private/tmp/ydbdoc-review-one-pass-v003
.venv/bin/python -m pytest \
  tests/unit/test_parser_round_trip.py \
  tests/unit/test_complete_document_validation.py \
  tests/unit/test_one_pass_translation.py \
  tests/unit/test_translation_transaction.py \
  tests/unit/test_front_matter.py \
  tests/unit/test_yfm_notes.py \
  tests/unit/test_yfm_cuts.py \
  tests/unit/test_yfm_tabs.py \
  tests/unit/test_yfm_includes.py \
  tests/unit/test_yfm_conditionals.py \
  tests/unit/test_atom_round_trip.py -q
```

## Notes for the reviewer

- Historical `ruff-baseline-v020.json` must remain byte-immutable
  (`677896e7eba1af6c884fecf42a9543b40ef70b0caf3bf7e4d98521e8e6ff6ba7`).
- Do not implement product code in the review response; verdict only.


## Resubmission after v003 CHANGES_REQUESTED

Four RED findings were fixed and covered by tests. Next review entrypoint:
`review-request-v004.md` → `response-v004.yaml`.
