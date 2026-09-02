---
task_id: TASK-51797-ONE-PASS-REMEDIATION
specification_version: "one-pass-remediation-v005"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v005.yaml"
implementation_authorized: false
---

# External review request: remediation v005 baseline contract

Review `specification.md` and `implementation-plan.yaml`. v005 retains approved
v004 and closes R-004: exhaustive 82-path out-of-union inventory, separation of
unchanged baseline presence from executor edit permission, exact gate ownership,
bootstrap snapshot and validation commands, required removal of unauthorized
`uv.lock`, and current control-artifact accounting. Confirm no silent scope
expansion and no developer choice.

Write only `response-v005.yaml`. APPROVED requires empty findings and
`has_unresolved_developer_choices: false`. Do not edit code.
