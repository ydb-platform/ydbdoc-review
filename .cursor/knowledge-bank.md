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
- **§6.228:** EN зеркала читать с tip `merge_base_with` (origin/main), не с checkout merge-commit. Иначе preserve тащит stale `#vklyuchenie-…`.

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
- **Gate (§6.226) `en_link_target`:** после финального EN-дерева — файл + `#fragment`; иначе 🔴. Не чинит сам.
- **§6.227:** preserve тоже repair; gate читает диск; redirect from→to для RU twin; baseline href fallback.
- **§6.228:** gate не блокирует ambient tip-main link debt; только новые битые hrefs этого прогона.
- **§6.229:** late repair + `en_link_target` читают **tip + written overlays**, не stale merge-commit worktree (иначе tip-only siblings «missing» и preserve ломается).
- **§6.230:** `{% include [x](…md) %}` не Markdown-ссылка для gate; пустой файл на диске ≠ missing; timeout translate → следующий model в chain.
- **§6.231:** EN уже на tip translation-ветки с чистым href-parity = scope satisfied (не требовать noop-файл в diff PR).
- **§6.232:** critic_only noop не restage-ит на новый tip (stale verify не затирает ручной href-fix).
- **§6.233:** tip-resolvable EN hrefs win over inverted tip→merge RU mirror delta (не затирать configuration-v1).
- **§6.234:** critic empty-JSON → resplit batch halves; batch_chars 2500.
- **§6.235:** YandexGPT safety refusal («не могу обсуждать») → heuristics-only verify, не `critic_execution_failed`.
- **§6.236:** href-parity: RU translit + declared EN slug OK когда `fragment_repair` мапит пару (без exact baseline slot).
- **§6.237:** verify href-parity: merge-base EN baseline + rebuild extra после grandfather (#51761).
- Пример #51711 / #40385: tip EN после merge translation PR; stale checkout не авторитетен.
- Docker build: ECR Public → fallback `python:3.12-slim` (Hub) при 429 (§6.229).

## Auth / pin

- `GITHUB_TOKEN` в env часто 403 на `gh`; unset → keyring. Запись в ydb: `YDB_GH_TOKEN`.
- Consumer: `ydbdoc-review@v0.1.0` (force-move с логическими фиксами).

## 2026-09-01 04:10 UTC
<!-- d8e0fc1cdab0a168 -->
- 2. **Причина в пайплайне** → фикс в `ydbdoc-review`, Memory Bank, pin `v0.1.0`
