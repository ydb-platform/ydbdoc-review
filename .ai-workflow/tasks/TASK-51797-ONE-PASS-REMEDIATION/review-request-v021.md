---
protocol_version: 1
task_id: TASK-51797-ONE-PASS-REMEDIATION
review_type: specification_reviewer
specification_version: "one-pass-remediation-v021"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v021.yaml"
---

# External review request: Ruff artifact mapping v021

Review only. Do not implement code.

Read `response-v020.yaml`, `specification-v021-amendment.md`,
`implementation-plan-v021-amendment.yaml` and v020 predecessor.

Verify that exactly three Ruff artifacts enter resolved R-004 with exact
allowed files/symbols, ownership, requirement IDs, absent-to-present states and
mapping precedence. Verify the baseline JSON has exact authoritative fields and
capture-once sole-writer lifecycle, while any fourth/generic/wildcard mapping is
RED. Confirm v020 Ruff semantics are unchanged, exact v020 predecessor,
four-file v021 self-seal plus exact `response-v020.yaml`, and final Ruff validate then refresh plus immediate
policy validate.

Write exactly one final response to
`.ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v021.yaml`.
