---
task_id: TASK-51797-ONE-PASS-COVERAGE-DELETION
specification_version: "coverage-deletion-v008"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-ONE-PASS-COVERAGE-DELETION/response-v008.yaml"
implementation_authorized: false
---

# External review request v008

Version v008 supersedes pending v007 and retains all its eight-blocker,
same-file, fence and fragment contracts. Review additionally the exact atomic
ordering for retirement of `critic_only` safeguards in `_apply_results_to_disk`.

Confirm safeguards remain until all harness/planner producers are migrated and
deleted; static producer/caller/deserializer/dynamic reachability is GREEN
before removal; the same-file apply-results test preserves generic identical-
output no-mutation coverage without an action special case; and compatibility
parsing/default mapping is forbidden. Confirm no developer choice remains.

Write only
`.ai-workflow/tasks/TASK-51797-ONE-PASS-COVERAGE-DELETION/response-v008.yaml`
with `APPROVED`, `CHANGES_REQUESTED`, or `BLOCKED`; preserve prior responses,
require empty findings for APPROVED, and do not authorize implementation.
