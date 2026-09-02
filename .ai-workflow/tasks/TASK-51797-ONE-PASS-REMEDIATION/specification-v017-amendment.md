## Final diff remediation v017

This amendment closes only the externally confirmed findings FINAL-001 through
FINAL-010 from `TASK-51797-ONE-PASS-FINAL-DIFF-AUDIT`. It resolves exact
predecessor v016. No other product, test, configuration or protocol change is
authorized.

### Closed corrections

1. **FINAL-001 / R-001.** Restore
   `tests/unit/test_fence_comments.py` at that exact path from base commit
   `9ff8edec9a26d3975306e20adca325c6eb9f77e6`. Retain in that file every
   read-only detector/blocking case for fence comments and text fences. Remove
   only expectations that invoke retired production writers. Do not use
   `test_fence_comments_read_only.py` as the sole replacement. The final
   migration manifest must record the restored path as present and the exact
   same-file test command as GREEN.
2. **FINAL-002 / R-003.** Replace the weak test
   `test_translate_workflow_forwards_en_toc_reachable_to_read_only_validators`.
   It must call `run_doc_translate`; mock only
   `build_en_toc_reachable_from_repo` to return one unique sentinel; spy the
   final read-only href/link validators; and assert every applicable validator
   receives that identical object. Direct `run_pr_translation` invocation and
   mocking any intermediate forwarding edge are forbidden.
3. **FINAL-003 / R-008.** Add one `run_doc_translate` workflow test whose source
   change is exactly `ydb/docs/ru/root-page.md`. Assert initial queue inclusion,
   counterpart `ydb/docs/en/root-page.md`, provenance guard execution, atomic
   publication on success, and zero publication on provenance failure. Do not
   add a production `/core/` special case.
4. **FINAL-004 / R-010.** Restore
   `tests/unit/test_placeholder_repair.py` from the base commit. Preserve its
   read-only protect-marker detection and publication-blocking cases in that
   same file. Delete only cases whose expected behavior writes a repair into a
   document. Add in the same file a table-cell local-repair case: invalid
   primary changes an atom or table structure, parser rejects it inside
   acquisition, valid fallback is accepted; exhausted invalid candidates leave
   the pre-repair document byte-identical and publish nothing.
5. **FINAL-005 / R-011.** Replace the reachable navigation label acquisition in
   `pipeline/navigation_merge.py::_translate_menu_labels`. Its inputs are the
   job's existing `TranslationJobManifest`, `TranslationChatOnce`, labels and
   glossary. It selects only the manifest's immutable `translate` primary and
   fallback pair and calls `AcquisitionController(role="navigation")` with the
   existing four-request transition table. The acquisition parser requires
   valid JSON, each requested RU label exactly once, no extra label, and a
   non-empty EN value before acceptance. Malformed/partial/exhausted output
   raises the typed acquisition-exhausted error and aborts the whole navigation
   transaction before staging or publication. Delete the shared
   `YandexLLMClient.chat` call, `setdefault` RU fill, and JSON-error RU mapping.
   Do not add a model slug, retry loop or RU-success fallback.
6. **FINAL-006 / R-012.** In
   `translation/local_repair.py::run_bounded_local_repair`, the parser passed to
   `AcquisitionController(role="repair")` must perform, before returning a
   payload: exact finding ID and block ID, non-empty replacement, immutable atom
   ID/hash/order equality, allowed UTF-8 range containment, exactly one target
   range, construction of the proposed full document, and
   `validate_complete_document(proposed, validation_context)`. A failure raises
   the existing protocol-invalid exception so acquisition advances to the next
   approved attempt. No rejected candidate may be inserted or counted accepted.
7. **FINAL-007 / R-014.** Critic and repair messages are built only from stable
   editable block records: block ID, EN editable prose, corresponding RU prose,
   allowed range, and atom IDs/hashes. They must not contain the full rendered
   document or raw code, configuration, directive or fence bytes. Repair gets
   exactly the selected block plus finding and glossary context. Add a spy test
   with unique secrets in code/config/directives and assert the secrets occur in
   no critic or repair request.
8. **FINAL-008 / R-016.** Define exactly one production function
   `translation/one_pass.py::validate_complete_document(document,
   validation_context) -> None`. It is read-only and checks, in this order:
   no protect-token leak; parser success; Markdown/YFM container parity;
   source-owned atom ID/hash/order equality; link/href/fragment/anchor rules;
   fence/config byte equality; residual-Cyrillic classification. Call this same
   function after base render, inside each repair acquisition parser before
   acceptance, after accepted insertion/re-critic, and immediately before
   staging. Delete the protect-token-only substitute. Add one isolated
   corruption test per invariant and assert rollback/zero publication.
9. **FINAL-009 / R-021.** Delete the entire dead module
   `src/ydbdoc_review/pipeline/skip_paths.py` and the entire legacy suite
   `tests/unit/test_translate_skip_paths.py`. Remove every remaining import and
   reference to `matches_translate_skip`, `filter_translate_changes`,
   `filter_path_set`, and `translate_skip_globs`. Preserve only the already
   approved navigation-only exclusion mechanism in `navigation/scope_planner.py`.
   Add a static absence test and keep universal RU Markdown translation plus
   RU/EN read-only verification coverage GREEN.
10. **FINAL-010 / R-009.** Update only
    `.ai-workflow/tasks/TASK-51797-ONE-PASS/implementation-report-v010.md` to
    record that `NOTE_TITLE` and `CUT_TITLE` are translated prose slots and
    `UnknownSegmentKindError` blocks before render/stage. Cite the exact GREEN
    note-title, cut-title and unknown-kind zero-stage tests. Do not change the
    already conformant production behavior for R-009.

### Completion gate

After all ten corrections, update the same-file migration evidence, run every
listed focused test plus the full suite and Ruff contract, update the report
truthfully, then run v017 `refresh-manifest` and immediately `validate`. Any
later byte change invalidates that result and requires another refresh plus
validate. No commit, tag, publication or translation rerun is authorized before
analyst whole-diff audit, independent tester and external implementation review.

