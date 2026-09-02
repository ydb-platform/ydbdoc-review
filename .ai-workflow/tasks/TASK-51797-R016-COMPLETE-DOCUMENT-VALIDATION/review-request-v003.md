---
protocol_version: 1
task_id: TASK-51797-R016-COMPLETE-DOCUMENT-VALIDATION
review_type: specification_reviewer
specification_version: "r016-parser-owned-v003"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-R016-COMPLETE-DOCUMENT-VALIDATION/response-v003.yaml"
---

# External re-review: FINAL-008 / R-016 v003

Review only. Do not implement code.

Resolve v001, v002 and then `specification-v003-amendment.md` / `implementation-plan-v003-amendment.yaml`. Verify the amendment closes only the YFM provenance contradiction:

1. exact plugin files/symbols are now allowed;
2. every required physical open/branch/close/include marker gets exact UTF-8 `source_span` at StateBlock consumption time;
3. virtual branch/tab closes get exact zero-width parser boundaries and cannot enter slice hashes;
4. parser source-map consumers prefer required metadata and fail closed without later regex/find/index or `map` fallback;
5. existing grammar recognition and tabs metadata are preserved;
6. exact tests cover multibyte, nesting, every token, corruption and forbidden shortcuts with no developer choice.

Write exactly one final response to `.ai-workflow/tasks/TASK-51797-R016-COMPLETE-DOCUMENT-VALIDATION/response-v003.yaml`.
