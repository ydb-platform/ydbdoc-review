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
- §6.227: href-parity/autotitle preserve тоже запускают fragment repair; gate предпочитает post-apply bytes на диске старому `PairRunResult.target_text`.
- Redirect-aware remap: если EN href остался на `redirects.yaml` `from`, а RU twin уже на `to`, сопоставлять заголовки через RU `to`; путь менять на EN `to` только когда этот EN-файл существует.
- После RU href restore сохранять same-slot EN baseline href, только если candidate fragment отсутствует, а baseline fragment реально объявлен.
- Пример #51711: wrong `#vklyuchenie-…` → red; `#enabling-the-node-authentication-and-authorization-mode` → green.

## Auth / pin

- `GITHUB_TOKEN` в env часто 403 на `gh`; unset → keyring. Запись в ydb: `YDB_GH_TOKEN`.
- Consumer: `ydbdoc-review@v0.1.0` (force-move с логическими фиксами).

## 2026-08-31 18:45 UTC
<!-- f506e1071473bec4 -->
- | Regression #51711 red/green | Да: `test_pr_51711_…` + `test_en_link_target_ok_when_fragment_matches` |
- 1. **Нет явного overlay-читателя** — при проверке *до* apply (или если delete ещё не применён) можно промахнуться. Сейчас это закрыто порядком: apply → late repair → gate.

## 2026-08-31 18:54 UTC
<!-- c74235236ebfb2e7 -->
- Итог: файл есть, фрагмента нет. Ссылка битая. Critic мог зеленеть, потому что такие href-only пары не всегда гоняют pair-heuristics, а проверка «якорь есть?» раньше могла бежать, пока соседний EN ещё не на диске.
- После записи перевода на диск гейт `en_link_target` проверяет: файл есть и `#fragment` объявлен. Нет → 🔴, публикация/commit блокируется. Сам гейт ничего не переписывает.
- Тесты: неправильный RU-якорь → red, правильный EN → green.
- Для реального бага #51711 это не меняет исход.
- Имеет смысл дорабатывать только если хочешь ужесточить реализацию (pre-apply overlay / AST-only). Это не багфикс, а рефакторинг.

## 2026-08-31 19:04 UTC
<!-- fb1cd404936921c9 -->
- Preserve оставил эти байты. Late repair на диске якорь поправил, но гейт читает **старый `pair.target_text`**, а не пост-repair диск → снова red. Это **баг у нас** в wiring §6.226.
- RU из #40385 добавляет ссылку на `monitoring_config.md#tls`. EN после перевода, скорее всего, получил заголовок без явного `{#tls}` → auto-slug вроде `tls-on-monitoring-pages`, а href остаётся `#tls`. Гейт тут **прав**: битый fragment. Корневая дыра: перевод/structural path не гарантирует сохранение явных `{#id}` с RU.
- Гейт блокировать битые якоря должен; ломается то, что он смотрит не на финальное дерево и что перевод роняет `{#id}`. Могу чинить это следующим шагом.
