---
protocol_version: 1
task_id: TASK-51797-ONE-PASS-REMEDIATION
review_type: specification_reviewer
specification_version: "one-pass-remediation-v014"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v014.yaml"
---

# External review request: approved-contract legacy tests v014

Review only. Do not implement code.

Read `specification-v014-amendment.md`,
`implementation-plan-v014-amendment.yaml`, approved v010/v012/v013 amendments,
and these worktree tests:

- `tests/unit/test_chunker.py`
- `tests/unit/test_image_alt_protect.py`
- `tests/unit/test_pipeline_orchestrator.py`

Verify exact propagation of the one EN negative fixture into two chunker loops,
the exact R-015 image-atom assertion update, the exact R-012 acquisition-error
assertion update, environment-only classification of three cache PermissionError
failures, and exact v013 predecessor/v014 self-seal. No production behavior,
fixture or unrelated test may change.

Write exactly one final response to
`.ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v014.yaml`.
