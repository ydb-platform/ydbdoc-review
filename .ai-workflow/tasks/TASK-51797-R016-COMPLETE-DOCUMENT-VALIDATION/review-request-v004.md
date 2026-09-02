---
protocol_version: 1
task_id: TASK-51797-R016-COMPLETE-DOCUMENT-VALIDATION
review_type: specification_reviewer
specification_version: "r016-parser-owned-v004"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-R016-COMPLETE-DOCUMENT-VALIDATION/response-v004.yaml"
---

# External re-review: FINAL-008 / R-016 v004

Review only. Do not implement code.

Resolve v001 through v003, then review `specification-v004-amendment.md` and `implementation-plan-v004-amendment.yaml`. Verify only this reconciliation:

1. note/cut title byte spans are captured from the active parser match and exclude quotes;
2. only those parser-owned title bytes are replaced in the canonical marker digest;
3. all syntax/attributes/delimiters remain byte-identical and every other marker remains raw-hashed;
4. empty versus absent titles, invalid metadata and residual-Cyrillic title behavior are fail-closed;
5. no later raw scan or developer choice remains.

Write exactly one final response to `.ai-workflow/tasks/TASK-51797-R016-COMPLETE-DOCUMENT-VALIDATION/response-v004.yaml`.
