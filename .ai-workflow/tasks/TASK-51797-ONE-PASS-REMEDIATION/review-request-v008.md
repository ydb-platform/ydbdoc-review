---
task_id: TASK-51797-ONE-PASS-REMEDIATION
specification_version: "one-pass-remediation-v008"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v008.yaml"
implementation_authorized: false
---

# External review request: R-004 snapshot count correction v008

Review this minimal amendment only. The approved v006 bootstrap incorrectly
treated the historical 85 tracked plus 29 untracked aggregate as the entry
count of the later immutable snapshot. The actual snapshot has 138 entries and
the same approved base commit. v008 also classifies the two v007 artifacts and
its own protocol artifacts so bootstrap has no developer mapping choice.

Read:

- `specification-v008-amendment.md`
- `implementation-plan-v008-amendment.yaml`
- `implementation-plan-v006-amendment.yaml`
- `implementation-plan.yaml`
- `review-request-v007.md`
- `response-v007.yaml`
- `/private/tmp/ydbdoc-review-one-pass-v003/.ai-workflow/tasks/TASK-51797-ONE-PASS/baseline-snapshot-remediation-v005.yaml`

Verify directly that the snapshot SHA-256, base commit, total count, unique path
count and status counts match v008. Confirm that the correction neither
recaptures the baseline nor expands unrelated implementation scope, and that
all post-capture protocol paths have deterministic classification.

Write only `response-v008.yaml`. `APPROVED` requires empty findings and
`has_unresolved_developer_choices: false`. Do not edit implementation code.
