---
protocol_version: 1
task_id: TASK-51797-ONE-PASS-REMEDIATION
review_type: specification_reviewer
specification_version: "one-pass-remediation-v020"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v020.yaml"
---

# External review request: fail-closed Ruff baseline v020

Review only. Do not implement code.

Read `specification-v020-amendment.md`,
`implementation-plan-v020-amendment.yaml`, v019 and v018 predecessors.

Verify that the exact base-derived 306-record baseline covers only untouched
Python files, every present changed Python file must be Ruff-clean, and
validation rejects changed-path drift or any new, moved, changed or multiplied
diagnostic. Confirm deterministic exact schema, capture-once behavior, no
count-only waiver, no `noqa`/config/formatting escape, exact v019 predecessor,
four-file v020 self-seal, and Ruff validation before final refresh plus immediate
policy validate.

Write exactly one final response to
`.ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v020.yaml`.
