# Project knowledge bank

Нормативные правила `doc_translate` / ydbdoc-review. Только как **должно** работать сейчас.

## Launch

- Production path: GitHub label `doc_translate` → реальный ydb CI (workflow pin `ydbdoc-review@v0.1.0`).
- Доработка translation PR: comment `/ydbdoc continue …` + label `doc_continue` (не ручной EN и не полный re-translate, если цель — проверить continue).
- Eliza / local `job --mode translate` — только если явно попросили; не подменять ими отладку label-пайплайна.
- Цель отладки: `doc_translate` / `doc_continue` → critic 🟢 **без** ручных правок EN.

## Merged source PRs

- На merged source PR label обязан запускать полный `run_pr_translation`, не verify-only.
- Нет EN → перевести; удалён RU → `delete_en` (§6.223).
- Направление: только EN changed → `translate_to_ru`; RU есть / RU changed → `translate_to_en`; `ru_changed ∧ en_changed` → skip (§6.76).

## Orphans и redirect tombstones

- Не создавать и не обновлять EN на путях `redirects.yaml` `from` (tombstones). Живой контент — по `to`.
- Critic `orphan_toc_page` = fail. Страницы вне toc graph удаляем или подключаем в toc; не «оживляем» переводом (как toc-orphans).
- Пример: #45949 — EN на tombstone `maintenance/manual/dynamic-config.md` = orphan; skip + не тащить через retarget (§6.224).
- Skip tombstones даже если pending EN попал в `en_toc_reachable`.
- `retarget_redirect_inbound_links`: исключать tombstone paths из `allowed_paths`.
- Completeness / translation-PR scope: tombstone EN считается already satisfied на verify.

## Fragments / якоря

- RU→EN: remap фрагментов на якорь парного EN heading (явный `{#id}` или EN auto-slug).
- Legacy Diplodoc-транслит RU (`#vklyuchenie-…`) — кандидат на remap (§6.225).
- После apply/retarget: повторный `repair_en_fragments` на диске.
- **Gate (§6.226):** после apply / на verify tip — детерминированная проверка всех relative EN `.md` ссылок: файл существует, `#fragment` объявлен; иначе 🔴 `en_link_target` с `available: …`, независимо от critic. Нужен, потому что href-only preserve не гоняет pair heuristics.
- Пример: `client_certificate_authorization.md` → `#enabling-the-node-authentication-and-authorization-mode` (#51711).

## Auth (gh)

- `GITHUB_TOKEN` в env часто даёт 403 на `gh`; для keyring — unset.
- Запись в ydb обычно через `YDB_GH_TOKEN`.

## Pin

- Consumer workflows: `ydbdoc-review@v0.1.0` (force-move вместе с логическими фиксами).

## 2026-08-31 18:01 UTC
<!-- 8785a0eb8f92f8d0 -->
- Независимое ревью: scope верный для #45949. Есть EN `concepts/node-authorization.md`, удаление старого EN, toc/index/redirects, правка `client_certificate_authorization.md` на `#enabling-the-node-authentication-and-authorization-mode`. Tombstone `maintenance/manual/dynamic-config.md` EN нет (правильно). Факты/ссылки/структура RU↔EN совпадают, блокеров нет.
- Остальные обязательные проверки: `PR-check` / `checks_integrated` ещё pending
- Mergeability: `MERGEABLE`, статус `BLOCKED` (ждут checks / review)

## 2026-08-31 18:12 UTC
<!-- 681df186d3dae472 -->
- Сделано §6.226 (`0bbc60f5cd3` / `v0.1.0`):
- resolve relative `.md`, проверка файла и `#fragment`;
- 🔴 независимо от critic; на translate блокирует commit, на verify — вердикт файла;
