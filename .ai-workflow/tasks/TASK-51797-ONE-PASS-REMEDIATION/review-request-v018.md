---
protocol_version: 1
task_id: TASK-51797-ONE-PASS-REMEDIATION
review_type: specification_reviewer
specification_version: "one-pass-remediation-v018"
status: waiting_for_reviewer
reviewer: specification_reviewer
expected_response: ".ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v018.yaml"
---

# External review request: close v017 findings v018

Review only. Do not implement code.

Read `response-v017.yaml`, `specification-v018-amendment.md`,
`implementation-plan-v018-amendment.yaml`, and v017/v016 predecessors.

Verify exactly three closures:

1. both role Literals include `navigation`, while navigation is bound only to
   `manifest.model_policy.translate` and still has no new pair/config key/slug;
2. the frozen slotted validation-context schema, tuple semantics, one exact
   construction site and same-object four-boundary lifecycle leave no API or
   architecture choice;
3. all unique alternate fence tests move into the required original file and
   the alternate file's exact final state is deleted after GREEN.

Also verify exact v017 predecessor, v018 four-file self-seal plus
`response-v017.yaml`, and final v018 refresh followed immediately by validate.
Write exactly one final response to
`.ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/response-v018.yaml`.
