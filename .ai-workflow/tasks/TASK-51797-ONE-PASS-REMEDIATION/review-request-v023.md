---
protocol_version: 1
task_id: TASK-51797-ONE-PASS-REMEDIATION
review_type: specification_reviewer
specification_version: "one-pass-remediation-v023"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v023.yaml"
---

# External review request: factual Ruff baseline correction v023

Review only. Do not implement code.

Read v023 spec/plan, v020-v022 and the current Ruff gate implementation. Verify
the reproduced arithmetic: 198 unchanged-base diagnostics plus 108 byte-frozen
historical-current diagnostics equals current 306, while the exact five active
paths remain zero. Verify schema v2, git-only enumeration excluding `.venv`,
base-absent file treatment, frozen byte hashes, multiset fail-closed semantics,
absolute executable resolution with relative metadata, equal config hash/version,
exact v022 predecessor, v023 self-seal and final gate order. No waiver or
unrelated Ruff fix is permitted.

Write exactly one final response to
`.ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v023.yaml`.
