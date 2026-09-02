---
protocol_version: 1
task_id: TASK-51797-ONE-PASS-REMEDIATION
review_type: specification_reviewer
specification_version: "one-pass-remediation-v016"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v016.yaml"
---

# External review request: chunker batch-count contract v016

Review only. Do not implement code.

Read `specification-v016-amendment.md`,
`implementation-plan-v016-amendment.yaml`, v014/v015 amendments, and current:

- `/private/tmp/ydbdoc-review-one-pass-v003/src/ydbdoc_review/segmentation/chunker.py`
- `/private/tmp/ydbdoc-review-one-pass-v003/tests/unit/test_chunker.py`

Verify that glossary batch counts follow intentional dense-placeholder
isolation, making `<100` obsolete rather than exposing a production regression.
Confirm the replacement tests exact greedy-boundary maximality without a new
arbitrary threshold, preserves v014 behavior, and closes v015 refresh plus exact
v016 self-sealing without developer choice.

Write exactly one final response to
`.ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v016.yaml`.
