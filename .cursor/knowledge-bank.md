# Project knowledge bank

Это самостоятельная краткая копия действующего контракта. При расхождении формулировок приоритет имеет `REQUIREMENTS_RU.md`; исторические Memory Bank и спецификации требований не добавляют.

## Scope

- Один зафиксированный снимок текущей базовой ветки определяет наличие и содержимое RU/EN.
- Source PR задаёт только добавленные и изменённые RU Markdown seed-файлы. Одновременное изменение RU и EN всё равно переводится RU→EN.
- Исторические удаления RU не выбираются и никогда автоматически не удаляют актуальный EN.
- Единственное расширение: рекурсивные внутренние Markdown-ссылки из выбранных RU-файлов на RU-цели без EN-аналога в снимке. Циклы дедуплицируются, максимум 20 дополнительных файлов.

## Preflight

- До создания model client суммируется `len(raw_ru_text)` всех выбранных существующих RU Markdown-файлов.
- Лимит: `250000`. Превышение даёт RED без модели, записи файлов и translation PR.

## Translation

- На файл: прочитать текущий RU из снимка, замаскировать структурные атомы, отправить всю естественно-языковую прозу одним логическим запросом, восстановить атомы, провалидировать документ.
- В тот же запрос входят переводимые code comments, Mermaid labels и front matter `title`/`description`.
- Старый EN не передаётся модели и не используется при сборке результата.
- После primary допускается один configured fallback только для timeout, network error, HTTP 5xx или provider/model unavailable.
- Empty, malformed или content-invalid response сразу делает файл RED. Повтора и fallback для такого ответа нет.

## Modes

- `doc_continue` запускает весь процесс заново и добавляет technical-writer context. Результаты и состояние прошлого запуска не используются.
- `doc_verify` только проверяет существующий EN и ничего не переводит.

## Publication

- Structurally unsafe output не записывается.
- Если хотя бы один output unsafe, safe peers публикуются в draft translation PR с RED verdict.
- Artifact отсутствует только при отсутствии safe diff.
- Asset публикуется только вместе с принятой страницей.
- Source PR comment содержит только translation PR, если он создан, verdict и cost. Подробности остаются в translation PR.

## Explicitly excluded

Не применять: `main`/`TOC`/`include`/`inbound`/`fragment` expansion; `provenance`; `reconciliation`; `coverage`; `checkpoint`; `resume`; old-EN reuse; differential merge; batching; resplit; content retry; `WITHHOLD`; `R-GL`.
