---
protocol_version: 1
task_id: TASK-51797-ONE-PASS-REMEDIATION
review_type: specification_reviewer
specification_version: "one-pass-remediation-v010"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v010.yaml"
---

# External review request: R-006 real-fixture classification v010

Review only. Do not implement code.

Read:

- `specification-v010-amendment.md`
- `implementation-plan-v010-amendment.yaml`
- `specification.md` section R-006
- `implementation-plan.yaml` requirement R-006
- `/private/tmp/ydbdoc-review-one-pass-v003/tests/integration/test_real_files_round_trip.py`
- `/private/tmp/ydbdoc-review-one-pass-v003/tests/fixtures/markdown_files/en/core/reference/ydb-sdk/topic.md` lines 251-255
- `/private/tmp/ydbdoc-review-one-pass-v003/tests/fixtures/markdown_files/ru/core/reference/ydb-sdk/topic.md` lines 251-257

Verify that the exact EN fixture is truly ambiguous under R-006 and that the
test-only negative classification resolves the legacy generic-test conflict
without weakening the parser, changing fixture bytes, attaching prose, hiding
other failures or leaving a developer choice.

Write exactly one final response to
`.ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v010.yaml`.
`APPROVED` requires empty findings and
`has_unresolved_developer_choices: false`.
