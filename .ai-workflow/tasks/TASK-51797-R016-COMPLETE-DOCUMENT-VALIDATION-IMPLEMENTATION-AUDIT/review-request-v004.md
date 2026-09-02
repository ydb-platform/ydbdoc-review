---
protocol_version: 1
task_id: TASK-51797-R016-COMPLETE-DOCUMENT-VALIDATION-IMPLEMENTATION-AUDIT
review_type: implementation_reviewer
specification_version: "R-016-v001..v006 + remediation-v025"
status: waiting_for_reviewer
expected_response: "tasks/TASK-51797-R016-COMPLETE-DOCUMENT-VALIDATION-IMPLEMENTATION-AUDIT/response-v004.yaml"
---

# External implementation review request v004

Resubmission after `response-v003.yaml` CHANGES_REQUESTED. Independently verify
the four RED findings are closed:

1. **FINAL008-IMPL-005 coverage:** parser failure, container reorder/nesting,
   atom duplication, link path/order, fence body/closing, YFM line/virtual-close
   fail-closed, front-matter protected matrix, strip final-newline preservation.
2. **Absolute `/ru/...#якорь` localization** in `_map_expected_destination`.
3. **YFM span line ownership** + virtual-close zero-width enforcement in
   `_record_from_token_map`.
4. **Strip block-scalar** `title: |-\n  Привет` → `title: |-\n  Hello` without
   invented trailing newline.

Worktree: `/private/tmp/ydbdoc-review-one-pass-v003` on
`agent/task-51797-one-pass-v003`. Do not self-approve from the implementor chat.
Write `response-v004.yaml`.
