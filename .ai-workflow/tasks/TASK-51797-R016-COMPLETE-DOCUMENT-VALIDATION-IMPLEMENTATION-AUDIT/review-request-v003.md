---
protocol_version: 1
task_id: TASK-51797-R016-COMPLETE-DOCUMENT-VALIDATION-IMPLEMENTATION-AUDIT
review_type: implementation_reviewer
specification_version: "R-016-v001..v006 + remediation-v025"
status: waiting_for_reviewer
expected_response: "tasks/TASK-51797-R016-COMPLETE-DOCUMENT-VALIDATION-IMPLEMENTATION-AUDIT/response-v003.yaml"
---

# External implementation review request v003

This package is ready for an **independent second model**. The implementor chat
did not self-approve. Read `READY-FOR-EXTERNAL-REVIEW.md` in this task folder,
then independently verify:

1. **FINAL008-IMPL-007 CLOSED** in
   `src/ydbdoc_review/translation/one_pass.py`: each acquisition attempt builds
   an attempt-local `CompleteDocumentValidationContext` with that attempt's
   `candidate_anchor_map`, validates, and freezes only after success. Prove
   `tests/unit/test_one_pass_translation.py::test_invalid_primary_cyrillic_anchor_does_not_poison_fallback_context`
   is GREEN and that a rejected primary cannot poison fallback expected anchors.

2. **FINAL008-IMPL-005** evidence covers the named v001–v005 categories with
   isolated proofs (not only pass counts): container/atom/link/fence/YFM/front-matter
   mutations; note empty vs absent title; cut empty title; missing/zero-width
   YFM `source_span` fail-closed; front-matter title+description five-style
   updates; forbidden-shortcut AST on ownership symbols; four-boundary /
   pre-stage identity already covered by one-pass/transaction tests.

3. **Remediation v025** mechanical green: Ruff validate against
   `ruff-baseline-v025.json`; policy validate against refreshed manifest;
   `ruff-baseline-v020.json` bytes unchanged; manifest self-entry untracked.

4. No regressions in the focused pytest suite listed in
   `READY-FOR-EXTERNAL-REVIEW.md`.

Write `response-v003.yaml` with `verdict: APPROVED` or `CHANGES_REQUESTED`.
Do not implement code in this review.
