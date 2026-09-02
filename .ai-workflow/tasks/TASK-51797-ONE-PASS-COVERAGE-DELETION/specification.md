# TASK-51797-ONE-PASS-COVERAGE-DELETION v008

## Цель

Удалить старую архитектуру перевода, запрещённую `one-pass-v010`, не потеряв
полезные регрессионные гарантии. Удаление выполняется только после появления
эквивалентной или более строгой проверки нового one-pass-конвейера и её зелёного
прогона. Совместимые обёртки, скрытые переходы в старую семантику и сохранение
старого поведения под новым именем запрещены.

Эта поправка не меняет `one-pass-v010`. Она задаёт закрытый порядок миграции
покрытия и удаления. Разработчик не выбирает, что сохранить или чем заменить.

## Классификация файлов

### A. Немедленно удаляемые translation-owned legacy-модули

После зелёных replacement-тестов удалить:

1. `src/ydbdoc_review/translation/differential.py`.
2. `src/ydbdoc_review/translation/critic_retranslate.py`.

Удалить их legacy-тесты:

1. `tests/unit/test_differential_translation.py`.
2. `tests/unit/test_differential_partial_seed.py`.
3. `tests/unit/test_differential_low_magnitude_patch.py`.
4. `tests/unit/test_low_magnitude_structure_gate.py`.
5. `tests/unit/test_critic_retranslate.py`.

Полезные гарантии и точная замена:

| Старая гарантия | Новый обязательный тест |
|---|---|
| изменённый RU действительно переводится | `test_many_segments_use_exactly_one_model_call` и production workflow test из раздела E |
| структура, код, ссылки и токены не теряются | `test_source_owned_atoms_round_trip_without_exposure_to_model`, `test_protect_token_leaks_are_blocking` |
| ошибка модели не публикует повреждённый результат | `test_invalid_response_fails_without_retry`, `test_lost_or_duplicated_atom_token_blocks_file`, `test_failure_in_last_file_discards_every_staged_file` |
| временная ошибка повторяется ограниченно, затем используется запасная модель | все acquisition-тесты из `test_one_pass_translation.py` и `test_translation_model_wiring.py` |
| критическое замечание может быть исправлено, но не запускает полный перевод | `test_one_pass_runs_bounded_one_block_repair_and_recritic`, `test_local_repair_exhausts_two_logical_attempts_without_extra_recritic` |
| ссылка на отсутствующую EN-страницу добавляет RU-зависимость без цикла | все четыре теста `test_translation_dependency_queue.py` |
| маленькое изменение не портит посторонние байты | больше не является продуктовой гарантией: v010 намеренно строит файл из RU; вместо неё обязательны атомарная публикация и защита source-owned atoms |
| старый EN не используется как seed/template | новый тест `test_existing_en_bytes_are_never_read_for_translation` из раздела E |

Удаление разрешено только одним коммитным состоянием: replacement-тесты уже
существуют и зелёные, после чего legacy-модули и legacy-тесты удаляются.

### B. Translation-owned harness, удаляемый только целиком после замены покрытия

Кандидат на удаление: весь `src/ydbdoc_review/harness/`:

`__init__.py`, `cases.py`, `context.py`, `critic_verdict.py`, `pair.py`,
`pr_context.py`, `pr_profiles.py`, `pr_runner.py`, `pr_state.py`, `pr_steps.py`,
`profiles.py`, `render.py`, `runner.py`, `state.py`, `steps.py`.

До удаления обязательно выполнить разделы E, F и G. Частичное сохранение
translation-owned harness запрещено. Если доказан любой production-import или
вызов, удаление останавливается и такой вход сначала переводится на v010.

Удалить после зелёной замены:

- `tests/unit/test_harness.py`;
- `tests/unit/test_harness_pr.py`;
- `tests/unit/test_harness_pair_toc_reachable.py`;
- `tests/unit/test_glossary_verify_alignment.py`;
- `tests/unit/test_verify_partial_realign.py`;
- `tests/unit/test_verify_realign_cap.py`;
- `tests/harness/test_regression_cases.py` и его fixtures только после переноса
  каждой применимой fixture в параметризованный one-pass regression test.

Сопоставление полезных гарантий:

| Legacy-гарантия | Обязательная замена v010 |
|---|---|
| parse failure и пустой файл не приводят к публикации | `test_empty_or_unparseable_ru_blocks_without_model_or_stage` |
| translate workflow и прямой перевод используют один алгоритм | `test_production_workflow_calls_only_one_pass_transaction` |
| verify не запускает перевод и не пишет EN | `test_verify_workflow_is_read_only_for_document_bytes` |
| неудача модели не сохраняет старый EN как успешный перевод | `test_translation_failure_stages_nothing_and_never_reads_old_en` |
| ссылки, заголовочные якоря, include и fenced code сохраняются | существующие atom-round-trip тесты плюс `test_real_markdown_fixture_round_trips_all_source_atoms` |
| отсутствующие EN-ссылки входят в общую очередь | существующие dependency queue тесты |
| результат нескольких файлов публикуется только целиком | существующие transaction tests |
| комментарии в коде не переводятся постобработкой | `test_code_and_fence_comments_are_source_owned_and_never_sent_to_repair` |
| critic-only, semantic-noop, EN-to-RU, bilingual skip, partial realign | намеренно удалённая семантика; вместо неё `test_all_added_or_modified_ru_markdown_selects_translate_ru_to_en_once` и forbidden-reachability проверки |
| регрессионные YAML cases | переносится ожидание сохранения source atoms, конечного verdict и отсутствия публикации при RED; ожидания старых mode/step/seed не переносятся |

### C. Смешанные модули, которые нельзя массово удалить

Сохранить read-only и не относящуюся к переводу часть:

- GitHub PR чтение, комментарии, создание ветки и атомарная публикация;
- reporting builder и форматирование отчёта;
- deterministic validators;
- navigation/deletion workflow;
- общий LLM transport для доказанно независимых потребителей.

Файлы `tests/unit/test_github_workflow.py`, `test_reporting_builder.py`,
`test_fence_comments.py`, `test_translate_file.py`, `test_pipeline_analyze.py`,
`test_href_parity.py`, `test_include_targets.py`, `test_markdown_layout.py` не
удалять целиком. В них удалить или переписать только тесты старой переводческой
семантики. Непереводческие тесты должны остаться зелёными.

### C.1. Закрытое решение по текущим падениям

| Кластер | Решение |
|---|---|
| `fence_integrity` (1), `href_parity` (2), `link_locale` (1) | сохранить read-only validator assertions; заменить harness finalizer прямым validator call к v010 bytes; ожидания автоматической мутации удалить |
| `placeholder_repair` (3) | сохранить detection/blocking assertions; writer assertions удалить, покрытие дают atom-round-trip и transaction rollback |
| `prose_cyrillic` (1) | сохранить обнаружение остаточной кириллицы как blocking QA; model writer удалить |
| `reinsert_coverage` (1), `renderer_coverage` (1) | сохранить lossless/token assertions и перевести fixture на v010 protect/restore/render API |
| `pipeline_analyze` (9) | удалить старые action expectations; заменить параметризованным universal RU Markdown action и сохранить отдельные navigation/deletion cases |
| `pipeline_orchestrator` (5) | переписать на v010 transaction, atomic failure и отсутствие old-EN read; pair plans удалить |
| `translate_file` (8) | удалить critic-only/verify/full-repair cases; accepted payload, read-only critic, bounded local repair и blocking verdict перенести в one-pass tests |
| `qa` (1) | сохранить read-only verdict aggregation без document mutation |
| `github_workflow` (6) | fixture создаёт достижимые pinned SHA и blob identities; provenance guard не отключать; недостижимый SHA покрыть отдельным blocking test |

#### Exact retirement of legacy fence normalization coverage

`tests/unit/test_fence_integrity.py` является смешанным read-only validator
suite и не удаляется. Существующий тест
`test_finalize_en_after_enforce_fixes_stroka_and_vm_in_indented_fence` нельзя
удалять и нельзя заменять только отдельным transaction test в другом файле.
Сохранить это же имя теста как traceable regression identity и полностью
переписать его body на эквивалентную гарантию v010:

1. Использовать тот же RU fixture с `#FQDN ВМ` и `<строка>` внутри fenced code.
2. Прямо вызвать production read-only detector и доказать, что он находит обе
   исходные недопустимые для публикации кириллические позиции с точным file/path
   location или стабильным finding identity.
3. Передать fixture через production v010 transaction path с writer spies,
   настроенными падать при любом вызове старого normalization/finalizer writer.
4. Доказать побайтовое равенство обоих source-owned fence bodies до и после
   protect/restore/QA. Ни `ВМ` -> `VM`, ни `<строка>` -> `<string>` не происходит.
5. Доказать blocking terminal verdict, ноль staged EN outputs, ноль publish/
   commit/push calls и наличие обоих detector findings в structured report.
6. AST/import/call assertion в этом же тесте доказывает, что production workflow
   не импортирует и не достигает `_finalize_en_target`,
   `normalize_ru_source_for_translation` либо любого fence normalization writer.
7. После зелёного переписанного теста удалить production normalization writer;
   read-only detector и read-only `fence_integrity` API сохранить.

Существующий дополнительный
`test_source_owned_cyrillic_fence_blocks_transaction_without_writer` сохранить:
он проверяет общую transaction-гарантию, а одноимённый переписанный legacy test
обеспечивает same-file историческую трассируемость. Они не являются заменой друг
друга и оба должны быть зелёными до удаления writer.

Старая гарантия «система молча переводит/нормализует кодовый блок и публикует»
намеренно отменяется v010. Полная эквивалентная safety-гарантия теперь такова:
условие обнаруживается, source bytes не меняются, публикация блокируется, старый
writer недостижим. Ослаблять detector assertion до простого `verdict != green`
или мокать detector запрещено.

#### Same-file migration contract for all current collection blockers

Tooling must be able to trace every retired expectation without interpreting a
cross-file coverage argument. The current collect has exactly eight blockers:
`test_glossary_verify_alignment.py`, `test_harness.py`,
`test_harness_pair_toc_reachable.py`, `test_harness_pr.py`,
`test_verify_partial_realign.py`, `test_verify_realign_cap.py`,
`test_prose_cyrillic.py`, and `test_final_tree_reader.py`.
`test_fence_comments.py` is no longer a blocker after its five read-only
replacements became GREEN, but its already-defined same-file contract remains
mandatory. Therefore all eight blocked files stay present
during migration and are collected without importing `ydbdoc_review.harness`.
Each listed test is first rewritten in the same file and run GREEN. Only then
may a harness-only file be deleted, and only in the same change that adds the
named traceable replacement to its final file. Mixed files remain permanently.

1. `tests/unit/test_fence_comments.py` remains. Keep every `collect_*` and
   `check_*` test as read-only detector coverage. Rewrite
   `test_finalize_en_target_translates_text_fence_increment_chain`,
   `test_translate_file_finalizes_fence_comments_via_llm`,
   `test_translate_pipeline_prose_then_multiple_fence_comments`, and
   `test_finalize_en_copies_code_then_translates_each_comment_line` in this
   same file to assert exact findings, unchanged fence bytes, zero writer
   calls, blocking verdict and zero staged outputs. Delete tests of standalone
   `translate_*` writer helpers only after the same-file replacements are green
   and writer symbols are absent. Never weaken collector location assertions.

2. `tests/unit/test_glossary_verify_alignment.py` remains until its sole test
   `test_glossary_verify_clears_alignment_error` is rewritten in place. Use the
   same glossary/YFM-tab fixture, assert v010 atom round-trip and read-only QA,
   no old-EN read, no alignment repair call, and blocking zero-stage behavior
   for malformed atom parity. Rename only after GREEN to
   `test_glossary_v010_round_trip_without_alignment_writer`; if the old file is
   then deleted, move that exact test unchanged to
   `tests/unit/test_one_pass_translation.py` in the same change.

3. `tests/unit/test_harness.py` is rewritten in place without harness imports.
   Retain traceability as follows: `test_parse_step_empty_file_stops_early` ->
   exact v010 empty-input/no-call/no-stage assertion;
   `test_harness_translate_matches_translate_file` -> rename to
   `test_production_workflow_matches_one_pass_transaction` and compare exact
   bytes/report; `test_translate_step_skipped_in_verify_profile` -> rename to
   `test_verify_workflow_is_read_only_for_document_bytes`;
   `test_finalize_uses_ru_for_protected_href_and_en_for_fence_reference` ->
   rename to `test_v010_uses_ru_source_owned_href_and_fence_bytes`.
   The four old profile/finalize/semantic-noop expectations are rewritten to
   the universal action, blocking Cyrillic fence, transaction rollback, and
   no-old-EN-read assertions. After all eight are GREEN, move them unchanged
   into `test_one_pass_translation.py` or `test_translation_transaction.py`,
   then delete `test_harness.py` and the harness package in the same change.

4. `tests/unit/test_harness_pr.py` is rewritten in place without harness
   imports. `test_translate_pr_profile_steps` becomes
   `test_translate_pr_enters_one_pass_transaction_only`;
   `test_verify_pr_profile_steps` becomes
   `test_verify_pr_is_read_only`; `test_plan_verify_pairs_skips_missing_text`
   becomes `test_missing_ru_text_blocks_without_translation_call`; and
   `test_pr_harness_translate_matches_orchestrator` becomes
   `test_github_workflow_matches_one_pass_transaction`. Assert exact action,
   call cardinality, report/verdict and staged output. After GREEN, move exact
   tests to `test_github_workflow.py`, then delete the old file.

5. `tests/unit/test_harness_pair_toc_reachable.py` is rewritten in place.
   Preserve `test_run_pair_plan_forwards_en_toc_reachable_to_harness` under the
   new name `test_translate_workflow_forwards_en_toc_reachable_to_read_only_validators`
   with the exact sentinel contract from Section E. Convert missing-link,
   anchor and href cases to read-only detector plus atom/transaction tests.
   Convert LLM failure to zero-stage/no-old-EN fallback. Rewrite
   `critic_only`, href-only mutation, semantic-noop, structural/fence repair and
   empty-RU preservation expectations to assert those routes are unreachable
   and the corresponding transaction blocks or universal v010 action runs.
   All eleven tests must collect and pass before moving the unchanged useful
   cases to `test_github_workflow.py`, `test_href_parity.py`, and
   `test_translation_transaction.py`; then delete the old file.

6. `tests/unit/test_verify_partial_realign.py` is rewritten in place.
   `test_partial_verify_realign_translates_gap_segments_only` becomes
   `test_translate_uses_one_whole_file_payload_without_partial_realign` and
   asserts one payload plus no old-EN read.
   `test_round_trip_verify_restores_missing_heading_anchor_without_llm`
   becomes `test_verify_reports_missing_heading_anchor_without_mutation` and
   asserts read-only exact location. `test_partial_verify_realign_skips_when_too_many_pending`
   becomes `test_verify_never_enters_partial_realign_regardless_of_finding_count`.
   After GREEN, move these exact tests to one-pass/read-only QA suites and
   delete the old file.

7. `tests/unit/test_verify_realign_cap.py` is rewritten in place.
   `test_verify_repairs_legacy_layout_before_round_trip_gate` becomes
   `test_verify_reports_legacy_layout_without_repair`;
   `test_verify_finalize_keeps_en_body_ref_but_passes_ru_layout_ref` becomes
   `test_v010_validators_receive_ru_layout_and_do_not_write_en`;
   `test_verify_realign_skips_full_retranslate_for_large_files` becomes
   `test_verify_never_retranslates_large_files`. Assert detector locations,
   unchanged bytes, zero model/writer calls and no staging. After GREEN, move
   exact tests to read-only QA/transaction suites and delete the old file.

8. `tests/unit/test_prose_cyrillic.py` remains as a mixed read-only detector
   suite. Keep `test_collect_cyrillic_prose_spans_backticks_and_words` and
   `test_collect_cyrillic_prose_spans_skips_fenced_code` with their exact span
   and exclusion assertions. Rewrite
   `test_translate_cyrillic_prose_with_mock_fn` and
   `test_translate_cyrillic_prose_with_client_mock` in place as raising-writer
   spy tests proving detector output never invokes a model writer. Rewrite
   `test_translate_file_prose_cyrillic_finalize_clears_blocking_heuristic` as
   `test_one_pass_cyrillic_prose_finding_blocks_without_finalize_writer`, with
   exact finding location, unchanged bytes, terminal RED, zero staged outputs
   and zero publish calls. Run all five GREEN before deleting the removed writer
   import and writer symbol; collector APIs and tests remain.

9. `tests/unit/test_final_tree_reader.py` remains. Keep
   `test_final_tree_reader_prefers_tip_for_non_overlay` as read-only immutable-
   tree selection coverage. Rewrite
   `test_late_repair_does_not_rewrite_tip_href_against_stale_merge` in place as
   `test_final_tree_never_runs_late_fragment_repair_against_stale_merge`: use a
   raising spy for the deleted fragment helper, assert exact RU href atom or a
   blocking provenance finding, unchanged staged bytes and zero publication.
   Keep `test_en_link_gate_uses_tip_targets_for_preserved_overlay` as read-only
   target-existence coverage, but remove any writer/helper import. All three
   must collect and pass before deleting the old fragment helper. This file is
   never deleted.

For every move, `git diff` must show the GREEN rewritten body moved unchanged
apart from imports. A destination test must include the legacy source path and
old test name in a comment or parametrized case ID. Deletion before the
same-file GREEN run, replacing assertions with `assert not callable`, or using
skip/xfail is forbidden.

#### Same-file migration contract for fragment repair retirement

`src/ydbdoc_review/validation/fragment_repair.py` is mixed. Keep the read-only
`fragment_declared_in_markdown` API and its parser helpers. Delete writer entry
points `prefer_baseline_href_when_fragment_missing`, `repair_en_fragments`,
`_rewrite_href`, and every helper left with callers only from those writers,
but only after this same-file sequence is GREEN:

1. In `tests/unit/test_fragment_repair.py`, retain
   `test_fragment_declared_in_markdown` unchanged as read-only detector coverage.
2. Rewrite `test_repair_keeps_valid_fragment` under the traceable name
   `test_v010_preserves_exact_ru_href_atom_without_fragment_writer`. Feed an RU
   link with path, query, fragment, title and escaping through production
   protect/restore. Assert exact byte equality of the whole href atom, and spies
   prove neither baseline EN nor a fragment writer is read/called.
3. Rewrite `test_pr_45949_client_cert_legacy_translit_fragment` under the name
   `test_v010_cyrillic_anchor_proposal_rewrites_staged_inbound_links` using its
   original fixture. Assert the deterministic Cyrillic-source-anchor rule,
   one valid ASCII proposal, consistent rewrite of the staged target anchor and
   every staged inbound href, then global inbound validation GREEN. The model
   may propose only the ASCII name; code owns localization and replacement.
4. Rewrite `test_pr_48012_sessions_finds_sibling_when_ru_and_en_baseline_stale`
   as `test_v010_out_of_scope_inbound_anchor_change_blocks_transaction`. Assert
   an inbound link outside staged scope produces exact blocking finding,
   zero staged outputs, zero commit/push/publish calls and no attempted sibling/
   baseline retargeting.
5. Rewrite `test_pr_40385_prefers_valid_en_baseline_href_after_ru_restore` as
   `test_v010_never_prefers_or_mutates_to_baseline_en_href`. Make EN-content
   access and both writer entry points raising spies; assert the exact RU href
   atom survives and the transaction either validates it or blocks, never
   substitutes the baseline href.
6. Keep the complementary final-path tests
   `tests/unit/test_translation_transaction.py::test_transaction_rewrites_in_scope_inbound_cyrillic_anchor`
   and `::test_transaction_blocks_out_of_scope_inbound_anchor_mutation`. Add
   legacy source path/test IDs as case comments and assert zero publication on
   the blocking case.
7. Rewrite every remaining writer-oriented test in
   `test_fragment_repair.py` as a parametrized case of exact RU href atom
   preservation, deterministic staged-anchor rewrite, or blocking out-of-scope
   validation. Preserve original test names as case IDs. Tests that only cover
   `fragment_declared_in_markdown` remain unchanged.
8. Run the same-file suite and transaction suite GREEN. Then delete the writer
   symbols and dead private helpers. Run both suites GREEN again.
9. Add AST/import/call assertions proving production workflow, transaction,
   validators and tests cannot reach the deleted writer names and contain no
   baseline-link auto-mutation dispatch key.

The retired guarantee «repair an RU-derived href by selecting or inventing an
EN/baseline href» is not preserved. Its non-weakened v010 safety replacement is
exact source href atom preservation, constrained staged Cyrillic-anchor rewrite,
blocking out-of-scope inbound changes, and zero publication on failure.

#### Atomic ordering for critic_only apply-safeguard retirement

The `critic_only` guards inside `github/workflow.py::_apply_results_to_disk`
must not be removed while any harness, planner, CLI, workflow, test or dynamic
dispatch can still produce a `critic_only` plan. Apply this exact order:

1. Complete and run GREEN all same-file harness replacement tests in this
   amendment while the apply safeguards still exist.
2. Migrate every production caller to v010, delete all harness `critic_only`
   callers/action constructors and then delete the harness package according to
   Sections B/C. No change to `_apply_results_to_disk` is allowed yet.
3. Run a dedicated AST/import/call test
   `test_critic_only_has_no_producer_or_apply_reachability`. It must scan
   production source, action enums, serialized state loaders and string dispatch
   keys, and prove there is no producer, caller, deserializer or dynamic route
   from CLI/workflow/orchestrator to `critic_only`. Historical documentation
   text is excluded; executable code/config is not.
4. Rewrite, in the same file, existing
   `tests/unit/test_apply_results_noop.py::test_apply_results_skips_identical_critic_only_noop`
   to `test_apply_results_skips_identical_one_pass_output_without_action_special_case`.
   Use a normal v010 result with output bytes identical to the checked-out
   target. Assert zero touched paths, zero commit/push/publish calls and no
   action-specific branch. Run GREEN before safeguard deletion.
5. Only after steps 1–4 are GREEN remove the `critic_only` branches from
   `_apply_results_to_disk`, remove the action/state value and delete dead
   helpers. Do this in one change with the caller removals, not earlier.
6. Run the reachability and rewritten apply-results tests GREEN again, then the
   full suite. Add a source assertion that `_apply_results_to_disk` contains no
   `critic_only` literal while retaining generic byte-identical no-op protection.

If any caller or deserializer remains, deletion is blocked. A compatibility
parser that accepts `critic_only`, a default mapping to v010, or removal of the
generic identical-output safeguard is forbidden.

Восемь текущих collection blockers не исправлять восстановлением
`critic_retranslate`. Их точная судьба:

| Файл | Обязательное действие |
|---|---|
| `tests/unit/test_fence_comments.py` | разделить: сохранить collectors и read-only blocking QA, перевести source-owned atom cases на v010 API; удалить model writer и harness finalizer cases |
| `tests/unit/test_glossary_verify_alignment.py` | удалить после зелёного `test_real_markdown_fixture_round_trips_all_source_atoms`, в который обязательно добавить glossary/YFM-tab fixture; старый partial alignment не сохранять |
| `tests/unit/test_harness.py` | удалить целиком после зелёных replacement tests раздела B |
| `tests/unit/test_harness_pair_toc_reachable.py` | удалить после зелёных dependency/atom tests и нового `test_translate_workflow_forwards_en_toc_reachable_to_read_only_validators` |
| `tests/unit/test_harness_pr.py` | удалить после зелёного `test_production_workflow_calls_only_one_pass_transaction` и transaction tests |
| `tests/unit/test_verify_partial_realign.py` | удалить после зелёных atom round-trip и bounded local repair tests; partial realign намеренно не сохранять |
| `tests/unit/test_verify_realign_cap.py` | удалить после зелёных atom round-trip, transaction rollback и read-only verify tests; legacy layout repair/retranslate cap намеренно не сохранять |

Ни один из этих файлов не переписывать в compatibility test старого harness.

### D. Старые writers и mutators

После перехода production workflow удалить:

- `src/ydbdoc_review/translation/repair.py`, если в нём нет read-only API;
- модельные writer-функции из `validation/fence_comments.py` и
  `validation/prose_cyrillic.py`;
- writer-функции из `validation/fragment_repair.py` и
  `validation/structural_repair.py`.

Read-only collectors/checkers из смешанных validation-модулей сохранить.
Файл можно удалить целиком только если статический анализ докажет отсутствие
сохранённого read-only API. Иначе удалить только writer symbols и их тесты.

Точная replacement-гарантия:

- source-owned atoms восстанавливаются детерминированно до critic;
- critic read-only;
- единственный model writer после принятого полного перевода находится в
  bounded local repair controller и может заменить только разрешённый prose
  block или предложить ASCII-якорь по правилам v010;
- любая невозможность детерминированного восстановления блокирует транзакцию.

Обязательные тесты: atom round-trip, bounded local repair, transaction rollback,
а также `test_forbidden_post_translation_writers_are_absent`.

## E. Новые тесты, которые необходимо добавить до удаления

Добавить эти точные тесты, если эквивалентного теста с тем же утверждением ещё
нет. Допускается другое имя только при сохранении приведённого утверждения.

1. `test_production_workflow_calls_only_one_pass_transaction`: monkeypatch старых
   entry points невозможен, workflow вызывает только новый transaction API.
2. `test_all_added_or_modified_ru_markdown_selects_translate_ru_to_en_once`:
   обычный RU Markdown, glossary и ранее пропускавшийся путь дают одну action.
3. `test_verify_workflow_is_read_only_for_document_bytes`: verify не вызывает
   translate/repair writer и не изменяет staged bytes.
4. `test_existing_en_bytes_are_never_read_for_translation`: content reader
   падает при попытке прочитать EN, но translation succeeds при разрешённой
   проверке path/blob identity.
5. `test_translation_failure_stages_nothing_and_never_reads_old_en`.
6. `test_empty_or_unparseable_ru_blocks_without_model_or_stage`.
7. `test_real_markdown_fixture_round_trips_all_source_atoms`: реальная fixture
   содержит ссылки с fragment/query/title, YFM include, explicit anchors,
   fenced code и конфигурацию; все atoms побайтово равны RU.
8. `test_code_and_fence_comments_are_source_owned_and_never_sent_to_repair`.
9. `test_forbidden_post_translation_writers_are_absent`.
10. `test_no_legacy_translation_modules_importable`.
11. `test_no_translation_imports_harness_package`.
12. `test_translation_action_enum_contains_only_v010_translation_action`.
13. `test_translate_workflow_forwards_en_toc_reachable_to_read_only_validators`:
    production test подменяет `build_en_toc_reachable_from_repo` уникальным
    sentinel-set, запускает translate workflow через v010 transaction и spy
    проверяет, что тот же объект/неизменённое множество передано во все
    применимые href/link read-only validator calls. Тест не должен вызывать
    harness и не может подменять сами production forwarding edges.

## F. Пошаговый delete-after-replacement plan

1. Зафиксировать inventory `git ls-files` для перечисленных модулей и тестов.
2. Запустить текущий полный test suite и сохранить baseline числа passed/failed.
3. Добавить тесты раздела E и перенести применимые regression fixtures.
4. Запустить новые тесты. Все должны быть зелёными до удаления.
5. Переключить production workflow/orchestrator на единственный v010 entry point.
6. Запустить новые тесты и сохранённые непереводческие GitHub/reporting/
   validation/navigation tests.
7. Удалить два legacy translation modules и пять legacy test files раздела A.
8. Удалить старые translation action values, planner branches и state fields.
9. Удалить model writers и EN mutators раздела D, сохранив read-only проверки.
10. Повторить статические проверки раздела G. При любом caller удаление
    останавливается, caller переводится на v010, затем проверка повторяется.
11. Удалить весь harness и перечисленные harness tests только когда production,
    CLI, workflow, package exports и сохранённые tests больше его не импортируют.
12. Запустить полный suite, Ruff и package import smoke test.
13. Сравнить итоговое число и список проверяемых v010 invariants с inventory.
    Уменьшение числа legacy-тестов допустимо, потеря перечисленной гарантии нет.

Нельзя сначала удалить тест, затем обещать replacement. Нельзя помечать старые
тесты skip/xfail. Нельзя оставить deprecated alias или compatibility shim.

## G. Статические проверки forbidden reachability

Все проверки обязательны и должны быть автоматизированы тестом или CI-командой.

1. AST import scan `src/ydbdoc_review`: нет imports из
   `ydbdoc_review.harness`, `translation.differential`,
   `translation.critic_retranslate`.
2. AST call/name scan production source: нет символов и строковых dispatch keys
   `critic_only`, `translate_en`, `semantic_noop`, `differential`,
   `low_magnitude`, `partial_realign`, `full_retranslate`.
3. Translation action enum содержит только `translate_ru_to_en_once` для
   добавленного/изменённого RU Markdown. Navigation и deletion являются
   отдельными непереводческими действиями.
4. Import smoke: импорт CLI, GitHub workflow и production orchestrator проходит
   при физически отсутствующем harness package.
5. Entry-point trace: CLI `doc_translate` -> GitHub workflow -> v010 transaction;
   ни один путь не достигает legacy planner/harness/writer/mutator.
6. Writer ownership scan: запись staged EN разрешена только transaction staging
   и bounded local repair insertion до финальной transaction validation.
7. LLM call ownership scan: translation roles вызываются только acquisition,
   read-only critic и bounded local repair; validators и reporting не вызывают
   LLM для изменения документа.
8. Тесты и fixtures не импортируют удалённые symbols. Документационные упоминания
   истории разрешены, исполняемые config keys и environment parsers запрещены.

Shared non-translation harness разрешено сохранить только если одновременно:

- он физически вынесен из `ydbdoc_review.harness` в нейтральный модуль;
- статический trace доказывает только непереводческих callers;
- он не содержит translation action selection, EN writer, retry, repair или
  old-EN access;
- отдельные tests доказывают его независимую функцию.

Иначе весь harness удаляется. Оставлять старый package «на всякий случай» нельзя.

## H. Критерий завершения

Поправка выполнена только если:

- все replacement-тесты зелёные до и после удаления;
- полный suite и Ruff зелёные;
- перечисленные legacy modules/tests физически отсутствуют;
- смешанные непереводческие гарантии сохранены;
- forbidden reachability scans зелёные;
- production translation имеет один достижимый алгоритм v010;
- отчёт содержит inventory deleted/retained/replaced и команды проверок.
