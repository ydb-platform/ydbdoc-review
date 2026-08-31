# Project knowledge bank

Нормативные правила `doc_translate` / ydbdoc-review. Только как **должно** работать сейчас.

## Launch

- Production path: GitHub label `doc_translate` → реальный ydb CI (workflow pin `ydbdoc-review@v0.1.0`).
- Доработка translation PR: comment `/ydbdoc continue …` + label `doc_continue` (не ручной EN и не полный re-translate, если цель — проверить continue).
- Eliza / local `job --mode translate` — только если явно попросили; не подменять ими отладку label-пайплайна.
- Цель: `doc_translate` / `doc_continue` → critic 🟢 **без** ручных правок EN.

## Merged source PRs

- На merged source PR label обязан запускать полный `run_pr_translation`, не verify-only.
- Нет EN → перевести; удалён RU → `delete_en` (§6.223).
- Направление: только EN changed → `translate_to_ru`; RU есть / RU changed → `translate_to_en`; `ru_changed ∧ en_changed` → skip (§6.76).

## Orphans и redirect tombstones

- Не создавать и не обновлять EN на путях `redirects.yaml` `from` (tombstones). Живой контент — по `to`.
- Critic `orphan_toc_page` = fail. Страницы вне toc graph удаляем или подключаем в toc; не «оживляем» переводом.
- Пример: #45949 — EN на tombstone `maintenance/manual/dynamic-config.md` = orphan (§6.224).
- Skip tombstones даже если pending EN попал в `en_toc_reachable`.
- `retarget_redirect_inbound_links`: исключать tombstone paths из `allowed_paths`.
- Completeness / verify scope: tombstone EN = already satisfied.

## Fragments / якоря

- RU→EN remap на EN `{#id}` / Diplodoc auto-slug; legacy RU-транслит (`#vklyuchenie-…`) — кандидат (§6.225).
- Late `repair_en_fragments` после apply/retarget (когда target EN уже на диске).
- **Gate (§6.226) `en_link_target`:** после финального EN-дерева (post-apply / verify tip) — детерминированно: relative `.md` существует, `#fragment` объявлен; иначе 🔴 с `available: …`, независимо от critic. Не чинит сам. Нужен, т.к. href-only preserve не гоняет pair heuristics, а pair-level `outbound_fragment` бежит до sibling targets на диске.
- Пример #51711: wrong `#vklyuchenie-…` → red; `#enabling-the-node-authentication-and-authorization-mode` → green.

## Auth / pin

- `GITHUB_TOKEN` в env часто 403 на `gh`; unset → keyring. Запись в ydb: `YDB_GH_TOKEN`.
- Consumer: `ydbdoc-review@v0.1.0` (force-move с логическими фиксами).
