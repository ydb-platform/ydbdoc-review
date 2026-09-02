---
protocol_version: 1
task_id: TASK-51797-ONE-PASS-REMEDIATION
review_type: specification_reviewer
specification_version: "one-pass-remediation-v015"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v015.yaml"
---

# External review request: final manifest lifecycle v015

Review only. Do not implement code.

Read:

- `specification-v015-amendment.md`
- `implementation-plan-v015-amendment.yaml`
- `implementation-plan-v014-amendment.yaml`
- `implementation-plan-v006-amendment.yaml`
- `/private/tmp/ydbdoc-review-one-pass-v003/scripts/remediation_policy_gate.py`

Verify the distinction between immutable capture-once snapshot and refreshable
generated final-delta manifest. Check deterministic complete regeneration,
atomic failure behavior, idempotency, exact mapping gates, quiescent timing,
post-change invalidation, v014 predecessor and exact v015 self-sealing. Confirm
the amendment stops its own manifest invalidation without trusting arbitrary
future paths or leaving a developer choice.

Write exactly one final response to
`.ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v015.yaml`.
