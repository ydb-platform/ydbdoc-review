# Канонические требования к переводу документации

Этот файл является единственным источником действующих требований к конвейеру перевода. Исторические спецификации, отчёты агентов и старые решения не изменяют этот контракт.

## 1. Назначение

Конвейер переводит изменения русской документации YDB на английский язык, создаёт отдельный переводной pull request и проверяет, что опубликованный Markdown не повреждён.

## 2. Запуск

- `doc_translate` запускает создание нового перевода и переводной pull request.
- `doc_verify` проверяет уже существующий английский текст без повторного перевода.
- `doc_continue` продолжает только job, у которой в артефактах состояния явно сохранён флаг продолжаемости и незавершённый этап после успешной фиксации SHA. Иные остановки требуют нового `doc_translate`.
- Повторная проверка не должна запускать новый перевод, если английский текст менять не требуется.

## 3. Выбор исходных файлов

- Исходный pull request задаёт только первоначальный список добавленных и изменённых русских Markdown-файлов.
- Для слитого старого pull request переводится текущая русская версия каждого такого файла из базы, на которой создаётся новый переводной pull request.
- Для слитого PR все решения scope (существование файла, fragment-owner зависимости, живой путь страницы) принимаются по зафиксированному tip этой базы (`merge_base_with`), а не по историческому merge-дереву, если tip уже переместил, удалил или сделал путь redirect-`from`.
- Если на tip `redirects.yaml` путь является redirect `from` (tombstone), конвейер не создаёт и не обновляет EN на `from`: пара — skip; completeness для tombstone удовлетворена. Exact-ASCII fragment owner при необходимости ставит в очередь живой tip-путь (`to`), не tombstone. Orphan gate и tombstone-skip читают один и тот же tip `redirects.yaml`.
- Если EN-путь ожидался в scope переводного PR, но не вошёл в diff ветки, completeness gap **не** ставится, когда tip EN уже объявляет все exact-ASCII `#fragment`, на которые ссылаются EN-страницы из diff этого PR (tip уже закрывает inbound-ссылки; translate мог быть noop относительно tip).
- Если `translate_to_en` падает, но конвейер сохраняет exact non-empty существующий tip EN (soft-keep), это не маскируется под успешный перевод: pair получает typed `translation_soft_keep` с нормализованной причиной, а PR-level blocker фиксирует путь и SHA-256 фактически публикуемых retained bytes. При отсутствии completeness/structural/integrity/critic blocker и наличии реального git artifact весь candidate публикуется только native draft/RED; soft-kept файл считается `Retained for manual repair`, а не переведённым. Отсутствующий/new target или raw error остаётся `WITHHOLD_INCOMPLETE`, unsafe evidence — `WITHHOLD_UNSAFE`, отсутствие commit/diff — hard `no_publishable_artifact` без пустого PR.
- QA-отчёт при completeness gap называет конкретные EN/RU пути и отличие «нет на tip» vs «есть на tip, нет в diff PR»; не маскирует блокер списком 🟢; не предлагает `doc_continue` как основной путь для gap «нет в diff».
- Русские файлы, исторически удалённые в исходном pull request, не входят в перевод.
- Историческое удаление русского файла никогда автоматически не удаляет и не перезаписывает актуальный английский файл.
- Если первоначальный русский файл отсутствует в текущей базе, операция останавливается до вызова модели, изменения файлов, commit и push.
- **R-GL-4a** — exact-ASCII fragment-owner closure в `plan_translation_scope` перебирает только внутренние href вида `.md#ASCII`, **новые** на diff-странице относительно `read_ru_base` (положительная дельта). Href, уже присутствующие в базовом RU до PR, не ставят owner в очередь, даже если tip EN не объявляет фрагмент (ambient debt). Для added-страницы при `read_ru_base` = `None`/пусто все href считаются новыми. Если tip EN owner уже объявляет exact-ASCII `#fragment`, owner не ставится для этого href.
- **R-GL-6a** — исключение к R-GL-4a для **include-владельцев** (`*/_includes/*.md`): при исходящих exact-ASCII fragment href со страниц `diff_ru_md` (включая pre-existing href, без фильтра `read_ru_base`) `plan_translation_scope` дополнительно ставит в `doc_ru` / `doc_from_main` RU-владельца include, если `_exact_ascii_fragment_owner_dependency` возвращает include-путь и tip EN не объявляет exact-ASCII `#fragment`. Non-include owners (например `auth_config.md`) по-прежнему только через R-GL-4a (новые href). **R-GL-6a.1** — новый href на diff-странице к missing include-фрагменту по-прежнему ставит owner. **R-GL-6a.2** — pre-existing href к missing include-фрагменту (например `connect.md#tls` на modified `authentication.md`) тоже ставит `_includes/connect.md`. **R-GL-6a.3** — pre-existing href к non-include owner не ставит owner (регрессия R-GL-4).

## 4. Зафиксированное состояние репозитория

- Перед переводом фиксируются конкретные Git SHA всех используемых состояний.
- Текущий русский текст, текущий английский текст, оглавления, redirects, ссылки и зависимости читаются из соответствующего зафиксированного SHA.
- До применения результата запрещены fallback-чтения из `HEAD`, рабочей директории, двигающейся ветки, merge base или upstream tip.
- Единственное допустимое чтение рабочей директории относится к результату, уже применённому в текущей транзакции перед commit.

## 5. Перевод одного файла

Для каждого файла выполняется один полный перевод:

1. Прочитать текущий русский файл целиком.
2. Заменить защищаемые элементы уникальными плейсхолдерами.
3. Один раз перевести оставшуюся прозу.
4. Вернуть защищённые элементы на исходные места.
5. Проверить полный восстановленный документ.

Защищаемые элементы включают:

- адреса и назначения ссылок;
- объявления якорей `{#...}` как структурные маркеры (значение якоря обрабатывается по §8);
- блоки и фрагменты кода;
- конфигурационные примеры;
- маркеры и границы YFM-конструкций (`{% note %}`, `{% cut %}`, `{% list tabs %}`, `{% if %}`, `{% include %}` и закрывающие теги);
- ключи, комментарии, невыбранные поля, разделители, стиль кавычек и chomping front matter;
- HTML и другие структурные атомы Markdown.

Переводимая проза внутри иначе защищённых конструкций (обязательно переводится тем же одноразовым проходом):

- значения front matter ключей `title` и `description`;
- заголовки YFM note / cut / tab, когда они присутствуют в исходнике;
- человеческие подписи поддерживаемых Mermaid sequenceDiagram и graph/flowchart
  узлов в квадратных скобках, с кавычками или без них, а также тела обычных `%%` комментариев.
  JSON-конфигурация `%%{init: ...}%%` остаётся неизменной.
  Каждая подпись является обычным сегментом `mermaid_label`, независимо от
  языка текста. Идентификаторы, стрелки, владельцы Note, ветвление и ограждения
  остаются защищёнными. Mermaid не отправляется в LLM целыми строками.

Mermaid-only файл с подписями имеет обязательства перевода по сегментам;
старый whole-file `materialize_protected` receipt не освобождает от них.
Неподдерживаемая грамматика остаётся защищённой; остаточная кириллица блокирует EN.

Любые другие ключи front matter не переводятся и сохраняются байт-в-байт в пределах защищённых зон.

Защита исходного литерала не разрешает остаточную кириллицу в финальном EN:
запрет языка приоритетнее публикации защищённых байтов. Такие литералы не
переписываются gate автоматически, а требуют ручного языкового исправления.

Шаблон Subject сертификата является узким детерминированным исключением из
обычной защиты inline code: полный Markdown-атом с точным содержимым
`Имя=Значение,...@<domain>` после восстановления href локализуется в
`Name=Value,...@<domain>`. Обёртка backtick и все байты вне содержимого этого
атома сохраняются. Более широкие code-атомы, произвольные пары ключ/значение,
fenced code и HTML-комментарии не переписываются по сходству. Русское написание
не имеет исключения в проверке EN: если оно осталось или было внедрено в любой
английский Markdown-файл, находка является блокирующей.

Старый английский текст не используется как источник частей нового перевода, шаблон склейки или основа частичной реконструкции.

План доказанного покрытия является отдельным fail-closed контрактом для будущего
исполнения по единицам. Он не считает существующий EN переводом только по возрасту,
размеру, числу или виду сегментов, похожему тексту, позиции или пустому diff B→R.
`reuse_verified` допустим только при явно переданном current
`CheckpointIdentity`, полном совпадении H0/H/B/R и translation fingerprint,
canonical unit key, source/target SHA-256 и единственном точном соответствии
текущему EN span с теми же protected atoms. Без current identity receipt не даёт
reuse. Изменённые href/include/anchor/code/config atoms, неоднозначные или
переставленные границы переводят план в `full` с явной причиной.

Для exact-fragment dependency planner получает fragment только из явной причины
scope planner. Обычная ссылка не является таким доказательством. Полный раздел с
уникальным explicit ASCII anchor выбирается до следующего заголовка того же или
более высокого уровня и вставляется или заменяется только между уникальными
стабильными EN anchors. Пустой раздел, повторный/сдвинутый anchor и неоднозначная
граница требуют `full`. Protected-only source может материализоваться
детерминированно. До включения исполнения в Task 7 `doc_translate` продолжает
полный проход и не применяет coverage plan.

**R-GL-4c** — отсутствие переводимых AST-сегментов отключает только вызовы
модели перевода и критика, но не завершает файловый pipeline. Для
protected-only, include-only, пустого и ASCII-only файла обязательны
детерминированные finalization, эвристики, вычисление verdict и report artifacts.
Остаточная кириллица, утёкший protect-placeholder, повреждённые fences и
отсутствующая EN-цель include остаются блокирующими. `FileRunState.stopped_early`
служит только диагностикой отсутствия model-translation и не означает успешную
валидацию. Точный пустой `existing_target_text` в verify не заменяется RU
исходником. No-segment alignment mismatch сохраняется как blocker и не запускает
model realignment.

**R-GL-4b** — пакеты перевода сегментов: non-overlapping, structure-aware, с учётом лимита JSON-ответа.

1. Запрещены overlapping batches, sliding window, merge пересечений.
2. Границы пакетов совпадают с границами сегментов AST; сегмент не разрезается между пакетами.
3. Перед `chunk_segments` сегменты длиннее `segment_max_source_chars` (default 1200) подразделяются `split_segment_for_batching` на внутренние структурные границы (`\n\n` для paragraph/blockquote/list item).
4. Размер пакета ограничивает оценку JSON-ответа: `estimate_translate_batch_output_chars` = сумма `len(text)` × `batch_output_expansion_ratio` (1.35) + `batch_json_overhead_chars` (512) + 40 × число сегментов; оценка ≤ `batch_max_output_chars` (6000). Дополнительно сумма source chars ≤ `segments_per_batch_chars` (2500).
5. Dense table cells (`placeholders >= 8`): solo batch; при нарушении бюджета сначала split, затем packing.
6. При `finish_reason=length` или empty JSON: один deterministic resplit пакета на два non-overlapping подпакета по границе сегментов (не более одного уровня). Irreducible monolith после resplit → `ManualAction` «segment exceeds safe translate output budget» и блокирующая ошибка (не soft-keep).

## 6. Связанные документы

- Зависимость добавляется в очередь, если внутренняя Markdown-ссылка из текущего русского файла указывает на путь, для которого в зафиксированном английском дереве **нет файла** (после нормализации `/ru/`→`/en/` и разрешения относительного пути). Наличие только в TOC без файла не считается «есть в EN».
- Устаревший, но существующий EN-файл зависимости сам по себе не добавляет её в очередь.
- Redirect на существующий EN-путь считается наличием EN; новая зависимость не ставится.
- Циклические и повторные ссылки на один и тот же RU-путь дедуплицируются.
- Все зависимости переводятся тем же полным одноразовым способом.
- Первоначальные файлы исходного pull request не расходуют лимит зависимостей.
- Общий лимит дополнительных зависимостей равен 20 файлам.
- После исчерпания лимита конвейер не начинает бесконечную рекурсию. Он оставляет явное предупреждение с путём ссылки и указанием на ручное действие.
- **R-GL-4a** — fragment-owner closure использует только **новые** exact-ASCII fragment href на diff-страницах. Существующий EN-файл зависимости без нового inbound fragment href не тянет full translate через fragment-owner.

## 7. Модель и повторные попытки

- При технической ошибке ответа разрешена повторная попытка.
- При недоступности выбранной модели разрешено переключение на другую настроенную модель.
- Технический повтор не превращается в повторную сборку английского файла из старых частей.
- После первичного перевода критик проверяет полученный документ.
- Для локальной исправимой проблемы модели передаётся ограниченный фрагмент, описание проблемы и необходимый контекст.
- Разрешено не более двух локальных попыток исправления одной проблемы.
- После каждой попытки критик повторно проверяет результат.
- Если проблема не исправлена, merge блокируется и пользователь получает понятный красный отчёт. Полный структурно безопасный candidate с repairable QA-blocker может быть опубликован только как draft/RED по R-GL-12–R-GL-13.
- Бесконечные циклы перевода и исправления запрещены.

Минимальный обязательный набор красных проверок критика и пост-валидации документа:

- остаточная кириллица во всём финальном EN-тексте, включая защищённые зоны,
  anchors, HTML, include, inline/fenced code и Markdown/YAML front matter;
- нарушенные protect-плейсхолдеры или их утечка в публикацию;
- нарушение структуры, ссылок, якорей или front matter по §8–§9;
- при наличии настроенного glossary — грубое нарушение обязательных терминов glossary помечается красным и блокирует merge так же, как прочие RED критика; публикация candidate регулируется отдельной осью R-GL-12.

Независимый `en_language` gate проверяет исходную строку финального UTF-8 артефакта
без исключений парсера после всех restores, critic fixes и поздних ремонтов.
Он работает в трёх FileHarness-профилях, на ранних возвратах pair, на финальном
дереве doc_translate и на immutable `verify_content_sha` в doc_verify.
Находка содержит номер строки, ограниченный preview и SHA-256 точных проверенных
байтов. Это `WITHHOLD_UNSAFE`, никогда не repairable `PUBLISH_RED`.
Пустой explicit overlay авторитетен; tombstone не воскрешается из baseline.
Dry-run читает pending overlay поверх frozen B, verify читает K независимо от
грязного worktree. K2 проверяется в существующем рекурсивном verify.
Ни gate, ни отчёт не подменяют проверенные байты: расхождение `target_text` или
`file_result.final_text` с авторитетным артефактом блокирует зелёное заключение.
Неязыковые блокеры и evidence непроверенных путей сохраняются.

Защищённые маркеры и синтаксис неизменяемы, но защита не подтверждает перевод
человеческого текста внутри атома. Критик и verify получают исходную `atom_map`
и отдельную `target_atom_map` из фактического целевого AST под source segment IDs
и согласованными именами маркеров. Русский исходный атом с уже английским целевым
payload не считается остаточной кириллицей. QA alignment, карта критика и render/apply
используют общий алгоритм сопоставления точной записи сертификата: маркеры целевого
текста совпадают с ключами его карты и адресуют фактические payloads.
Несовпадение количества/типа сегментов
или защищённых маркеров даёт `blocked`, category `protected_atom_alignment`;
известно неверное соответствие не заменяется исходной картой.

Детерминированная проверка целевых `code:` payloads после batching/resplit
выдаёт `blocked`, category `protected_atom_language`, `suggested_text: null`
при остаточной кириллице, даже если модель ответила `ok`. Оба прохода получают
текущий финализированный текст, повторный проход строит карту заново после fixes.
Категории атомов не разрешают model replacement маркера и не удаляются фильтром
skipped/identical segments или при отказе модели. Только повторная проверка
исправленного target снимает детерминированную языковую находку. Direct callers
без полного документа (`translated_text=None`) используют исходные атомы как
консервативный compatibility default; явно переданный пустой target авторитетен.

**R-GL-5a**: `critic_model_refusal` означает незавершённую проверку языка и стиля.

Когда LLM-критик возвращает safety/content-policy отказ (§6.235, `is_model_refusal_text`), а детерминированные эвристики файла **не** содержат blocking-сообщений:

1. Файловый `verdict` равен **`warnings`**. Независимой полной проверки языка и стиля нет, поэтому чистая проза не является исключением.
2. PR-level рекомендация merge (`_merge_recommendation`) **🟡**, если нет других blocking findings, completeness gaps и nav blockers.
3. Отказ остаётся warning issue в `critic_unresolved` и в списке «Что исправить». Требуется ручная проверка языка и стиля перед merge.
4. Отказ в любом batch сохраняет все замечания остальных batches, включая blocked `protected_atom_language`. Первый отказ завершает цикл без спекулятивного model repair. Отказ на любом последующем проходе, включая третий verify, сохраняет весь текущий объединённый результат и накопленные неприменённые замечания предыдущих проходов. Успешное исправление удаляет прежний диагноз из ожидающих исправления по сегменту, категории и точному комментарию; подтверждённый после исправления блокер остаётся нерешённым. История `critic_skipped` и `critic_applied` сохраняется; исправленные замечания не возвращаются в нерешённые или skipped-раздел отчёта, дубли атомных findings не добавляются. Непочиненные blockers сохраняют красный отчёт и запрет публикации, даже если присутствуют в списке skipped.
5. `warnings` с пустым списком issues в compatibility response не превращается в `ok`; `blocked` не понижается. ASCII-документы без сегментов проходят детерминированные проверки без запроса к критику.

**R-GL-5a.1**: Классификация finalize-warning `critic_model_refusal:` в `_classify_heuristic`: bucket **`warnings`**.

**R-GL-5a.2**: `humanize_heuristic("critic_model_refusal: …")` и `format_critic_reviewer_detail(category="critic_model_refusal", …)` сообщают, что проверка языка и стиля не завершена и требуется ручная проверка. Чистые эвристики не дают разрешения merge.

**R-GL-5a.3**: Отчёт выводит refusal как предупреждение с humanized RU текстом. После pair-level repair и пересчёта QA warning issue остаётся в `critic_unresolved` и сохраняет жёлтую рекомендацию.

Замечание, присутствующее одновременно в unresolved и skipped, выводится ровно
один раз как unresolved независимо от `include_skipped_critic` (по умолчанию,
`true` или `false`). Исключение дублей меняет только представление отчёта;
машинные blockers, `critic_skipped` и запрет публикации сохраняются.
Если отказ уже показан как critic issue, его эвристическое зеркало с кодом
`critic_model_refusal:` не создаёт второе замечание в отчёте. Сопоставляется код
события, а не похожий текст. Другие эвристики и отдельные critic findings остаются.

**R-GL-5b** — Поведение **`critic_execution_failed`** (пустой JSON после retries, невалидный JSON после repair+fallback, иные технические сбои критика) **не меняется**: `verdict=blocked`, merge 🔴, сообщение §6.238. Safety refusal и execution failure — **разные** категории; R-GL-5 не сливает их.

**R-GL-5d** — Для EN Markdown детерминированная проверка видимого AST выдаёт
warning для двух узких incident-форм: пробела в начале или конце видимого текста
ссылки (`editorial_link_label_space`) и точной регистронезависимой коллокации
`ldaps schema`, включая `` `ldaps` schema `` (`editorial_ldap_scheme`). Проверка
обходит параграфы, заголовки, определения терминов, списки, цитаты, таблицы и
вложенные YFM-контейнеры; inline-code внутри текста ссылки учитывается. Front
matter, HTML-комментарии, fenced/indented code и назначения ссылок не являются
видимым prose и исключены. Сообщение сохраняет номер и контекст исходной строки,
а отчёт предлагает убрать краевые пробелы или использовать `ldaps scheme`.
Контент автоматически не переписывается. Обе находки дают `warnings` и жёлтый
verdict даже при `critic=ok`; это не полная проверка английской грамматики или
стиля. `critic_model_refusal` остаётся самостоятельным warning и не исчезает при
чистом результате этих двух проверок.

## 8. Ссылки и якоря

- Внутренние пути и фрагменты ссылок должны сохраняться без потери.
- Если русский якорь состоит из ASCII-символов, английский якорь должен совпадать с ним байт в байт.
- Если русский якорь содержит кириллицу, английский документ получает отдельный английский якорь.
- Все английские ссылки на такой кириллический якорь должны согласованно использовать английское соответствие.
- В пределах одной job для всех переводимых файлов используется единый словарь соответствий кириллический→английский якорь. Повторный перевод того же исходного якоря не создаёт второе английское значение.
- Абсолютный путь `/ru/...` в английской документации заменяется на `/en/...` вместе с корректной локализацией фрагмента.
- Относительные внутренние пути локализуются в английское дерево без изменения смысла назначения.
- Порядок ссылок и их назначения не должны меняться самопроизвольно.
- Explicit/stable ASCII-фрагмент внутренней ссылки после translate сохраняется byte-identical. Узкое исключение: неявный RU Diplodoc auto-slug или legacy-транслит заголовка без `{#…}` может перейти в auto-slug единственного выровненного EN-заголовка без `{#…}`.
- Перед финальным gate конвейер обязан найти единственного детерминированного RU-владельца такого фрагмента (страница или aligned include), если фрагмент совпадает с explicit `{#id}`, Diplodoc auto-slug **или** legacy-транслитом заголовка, и объявить тот же id на парном EN-заголовке (`{#exact-id}`), включив EN-цель в candidate overlay. Неоднозначный pairing — fail closed; gate `en_link_target` остаётся блокирующим.
- Remap фрагмента в EN-only id разрешён для кириллицы и для доказанного неявного RU-heading→EN-heading auto-slug соответствия; explicit ASCII id не remap-ится.
- **R-GL-6b** — после apply, если `add_explicit_ascii_fragment_anchor` вернул `None` для уникального RU include-владельца с explicit `{#frag}`, вызывается `declare_explicit_fragment_on_include_owner(en_md, ru_md, frag)`: append-only `{#frag}` на единственный EN-заголовок без anchor с `diplodoc_auto_slug(title) == frag`, иначе на единственный EN-заголовок с keyword overlap (токены ≥3) с RU-заголовком-носителем; при неоднозначности — `None` (gate остаётся блокирующим). **R-GL-6b.1** — fallback не подменяет R-GL-6a translate include-владельца при real-tip outline mismatch.
- **R-GL-7** — exact-ASCII fragment parity является жёстким инвариантом до любого baseline/grandfather fast-path в `check_href_parity`. Для сопоставленных внутренних link-slots непустой fragment current RU после URL-decode, состоящий только из ASCII, должен совпадать с EN byte-identical. Совпадение candidate с tip EN baseline, наличие другого объявленного EN-якоря, dictionary/remap, отсутствие target-файла и ambient debt не разрешают подмену ASCII fragment. При одинаковом path сначала вычитаются точные href-occurrences, чтобы EN extras не сдвигали pairing; при локализованном/redirect path и равном числе link-slots используется позиционное сопоставление. Кириллические RU fragments остаются в действующем механизме локализации.
- **R-GL-8** — R-GL-7 не считает стабильным id неявный auto-slug русского заголовка. До ASCII blocker-а разрешается только доказанное соответствие: EN href разрешается в читаемый EN target, существует RU twin, outlines имеют одинаковое число и уровни заголовков, ровно одна aligned-пара без explicit anchors даёт исходный RU Diplodoc auto-slug/legacy-транслит и целевой EN auto-slug. Missing target, outline drift, ambiguity или explicit anchor с любой стороны оставляют blocker.
- **R-GL-9** — разрешимость baseline href не доказывает семантическую эквивалентность section target. `prefer_baseline_href_when_fragment_missing` не заменяет source-owned ASCII fragment другим baseline fragment; исключение только то же доказанное implicit heading auto-slug соответствие из R-GL-8. При неравном числе link-slots path-only restore допустим только для final EN candidate slot, который доказан четырьмя снимками: уникальный `(normalized label, decoded full href)` occurrence в RU base/current, historical RU-base/tip-EN slot, и unique identical ASCII fragment на обеих historical сторонах. Delete+add и duplicate historical fragment остаются blocker, а не pairing.
- **R-GL-10** — pre-existing stable-fragment href на source-diff странице ставит RU owner в translation scope, только если RU target однозначно объявляет fragment, а EN target его не объявляет. Broken RU href без RU declaration не расширяет scope. Для такого ambient broken RU path разрешено сохранить tip EN path только с тем же decoded fragment, когда current RU `path#fragment` не разрешается в immutable `ru_content_ref`, candidate target не разрешается в final EN tree, а baseline target разрешяется в ней. Подмена fragment запрещена. Это уточняет R-GL-4a и §6.233, не отменяя их защиту от произвольного ambient scope.
- **R-GL-11** — post-translate обработка обязана использовать две временные проекции: финальный EN candidate читается поверх `merge_base_with`, а RU-владелец exact fragment читается из `ru_content_ref` исходного PR. После declaration и late repair, непосредственно перед final `apply_en_link_target_checks`, выполняется fail-closed final-tree reconciliation; она меняет только path и сохраняет raw candidate fragment. Приёмочный тест проходит полный локальный маршрут `load contents → run_pr_translation → apply → declare → late repair → final reconciliation → en_link_target` на production-shaped #40385 fixture с разными non-empty RU base/current и EN tip commits, 3 current против 2 baseline links, и одновременно проверяет `security-auth` и `certificate-auth-config`; helper-only тесты недостаточны.
- **R-GL-12** — публикация paid candidate отделена от merge readiness typed-осью `WITHHOLD_INCOMPLETE | WITHHOLD_UNSAFE | PUBLISH_RED | PUBLISH_NORMAL`, с приоритетом incomplete > unsafe > repairable RED > normal. Pair error, отсутствующий/new ожидаемый output, нематериализованный soft-keep, source-retaining `ManualAction`, segment alignment failure, deterministic link-contract failure, protect-marker leakage и invalid mandatory navigation YAML запрещают prepare/commit/push/PR. Полный structurally safe candidate может быть draft/RED только для явного allowlist PR-level blockers: `en_link_target` и `translation_soft_keep`, причём у каждого soft-kept target обязаны существовать exact non-empty tip bytes и durable SHA-256 фактически опубликованного artifact. Остальные blocker classes остаются withheld.
- **R-GL-13** — published-blocked candidate обязан иметь native draft state, явный `QA RED, do not merge` banner и blocker summary в translation PR, красную merge recommendation в полном отчёте и `published_red` в source summary, CLI и ops ledger. Soft-keep summary отдельно показывает `Translated`, `Retained for manual repair` и `Failed without target`, называет path/reason/manual action и направляет к ручной правке translation branch с последующим `doc_verify`, а не к `doc_continue`. Durable `translation_soft_keep` снимается standalone/inline verify только когда bytes изменились относительно manifest hash и matching pair прошла current structural/integrity/critic validation; PR автоматически ready не становится. Exit 0 после успешного создания draft разрешает downstream CI и означает только успешную публикацию artifact; отсутствие translation PR или `no_publishable_artifact` остаётся hard failure. Existing ready-for-review PR перед продолжением переводится обратно в draft.
- **R-GL-14** — `doc_verify` может автоматически восстановить потерянную Markdown-обёртку ссылки только по immutable frozen-B evidence. H0 и H/R должны содержать byte-identical source paragraph; B должен содержать ровно одну ссылку с английским label, а K должен содержать этот label ровно один раз как plain complete-word text в соответствующем неизменённом source-owned paragraph. В K вставляется только обёртка `[label](B-href)` без изменения label, href, fragment и остальных байтов. B target обязан безопасно оставаться внутри настроенного `docs_root/en/core` как при raw component walk, так и после canonical resolve; leave-and-return traversal, cross-locale/encoded traversal, ambiguous occurrence, code/comment/existing-link/image/include/title context, missing final target или fragment блокируют proposal. Граница слова учитывает alphanumeric, `_` и Unicode combining categories Mn/Mc/Me. При единственном proposal обычный result writer первой verify-фазы не запускается; repair проходит существующий lease, создаёт ровно один K→K2 commit/push и требует fresh recursive verify K2. K2 no-proposal apply обязан быть пустым no-op без writer, touched paths и второго push. Typed `validation_issues` и `link_contract_issues` всегда видны в отчёте и блокируют merge до успешного repair/verify.
- **R-GL-15** — после собственного подтверждённого inline push K→K2 recursive `doc_verify` обязан ограниченно дождаться видимости K2 в свежем GitHub REST PR context. Разрешено не более шести REST-чтений и ожидания 1, 2, 4, 8 и 15 секунд, суммарно не более 30 intentional seconds. Только точный предыдущий owned K считается transient и допускает retry. K2 принимается лишь при неизменных destination ref и local checkout, равных K2 до и после REST-read; полный доказанный K2 context передаётся во внутреннюю рекурсию без нового непроверенного чтения. Любой третий SHA, ref/repository/state drift, lease/checkout drift или исчерпание stale K завершается fail-closed. Основной инвариант `remote E == PR head == checkout C` не ослабляется. Подмена или исчезновение authority body/evidence сохраняет контракт `ValueError`; структурный drift публикации сохраняет `RuntimeError`.
- **R-GL-16:** orphan gate должен признавать Markdown-фрагмент достижимым по транзитивной цепочке структурных YFM include от обычной TOC-reachable страницы. Доказательство использует только frozen B с pending output при translate либо проверяемый K с pending output при verify; exact pending key, включая пустой текст, перекрывает baseline. Missing/deleted/errored output не восстанавливается из HEAD, RU или worktree. Допустимы только существующие targets той же локали без выхода за её границу на любом шаге raw path traversal; циклы ограничиваются visited set. Include определяется только токеном `yfm_include` существующего Markdown tokenizer: обычные ссылки, code, comments и front matter доказательством не являются; fallback к line scan при ошибке запрещён. QA verdict не отменяет существование материализованных candidate bytes. Освобождение от orphan не снимает другие blockers, не изменяет href stripping и не создаёт blanket exemption для `_assets`.

## 9. Сохранность документа

После восстановления должны точно сохраняться:

- структура Markdown и YFM;
- ограждения, язык и содержимое блоков кода, кроме переводимых подписей Mermaid
  по §5; вне распознанных диапазонов подписей каждый байт Mermaid неизменен;
- контейнеры, вкладки, заметки и их границы;
- front matter, стиль YAML и наличие завершающей новой строки;
- защищённые ссылки, конфигурации и атомы;
- порядок структурных элементов.

В опубликованном тексте запрещены служебные protect-маркеры, включая `⟦...⟧` и их percent-encoded формы.

Для Mermaid проверяются полная поддерживаемая грамматика, сбалансированный стек
ветвей и точное равенство синтаксического каркаса. Перевод не меняет ID, типы и
направления стрелок, владельца и сторону Note, ключевые слова и вложенность ветвей,
а также `YDB` и `life_time` внутри подписей. Новые строки и управляющие разделители
в подписях отклоняются; допустимая буквальная пунктуация экранируется Mermaid entities.
Повторная финализация сохраняет проверенные переведённые подписи.

## 10. Изменения после исходного pull request

Следующие ситуации создают жёлтое предупреждение, но не блокируют перевод:

- русский файл изменился после исходного pull request;
- английский файл изменился после исходного pull request;
- английский файл был создан или удалён после исходного pull request.

В этих случаях переводится текущая русская версия целиком и **целиком заменяет** текущий английский файл результата. Осознанные EN-only правки после исходного pull request не сохраняются: это принятая политика overwrite с жёлтым предупреждением. Предупреждение показывает пути и затронувшие их коммиты. Оно не попадает в список полноты, не отменяет применение результата, commit или push.

Блокируют перевод:

- несовместимое расхождение истории;
- конфликт, при котором исходный pull request сам меняет соответствующий английский файл;
- отсутствие требуемого текущего русского файла;
- повреждение структуры, плейсхолдеров или документа после исчерпания разрешённых исправлений.

## 11. Публикация

- Все результаты сначала проверяются как единое финальное дерево.
- Изменения применяются транзакционно. Частичный или повреждённый результат не подготавливается к публикации.
- Полный безопасный результат проходит stage, commit и push в переводную ветку; incomplete или unsafe результат не публикуется.
- Создаётся открытый переводной pull request: обычный для clean candidate либо draft с явным RED для допустимого repairable blocker.
- На него устанавливается `ok-to-test`.
- Готовность означает зелёный `build-docs` и зелёный отчёт `doc_verify` на одном и том же SHA переводной ветки.
- Workflow не ждёт бесконечно: при отсутствии зелёного `build-docs` в пределах обычного CI-ожидания job оставляет жёлтое или красное состояние с указанием проверить checks, но не сообщает `success`, пока PR не создан и блокирующих ошибок перевода нет. Отсутствие зелёного `build-docs` из-за очереди CI само по себе не откатывает уже созданный PR.
- Workflow не имеет права завершаться с `success`, если блокирующая ошибка привела к пропуску commit, push или создания pull request. `success` после создания draft/RED означает только успешную публикацию artifact для запуска downstream CI и не означает merge readiness.

## 11.1 Навигация и redirects

- Если в результате job появляется новый английский Markdown-файл, соответствующий странице, которая в русском TOC достижима, конвейер обновляет английский TOC (и при необходимости redirects) так, чтобы новая EN-страница была достижима из EN TOC.
- Если русский источник был только файлом без TOC-достижимости, новый EN не обязан добавляться в TOC.
- Обновления TOC/redirects входят в то же финальное дерево и ту же транзакцию commit, что и переведённые страницы.

## 12. Отчёт

- Красные блокеры и жёлтые предупреждения показываются отдельно.
- Сообщения написаны понятным языком и содержат файл, причину и требуемое действие.
- Внутренние имена классов, плейсхолдеров и служебные маркеры не заменяют пользовательское объяснение.
- Для изменений после старого pull request показываются старый и текущий идентификаторы содержимого и затронувшие коммиты.

**R-GL-5c**: При единственном замечании `critic_model_refusal` и чистых детерминированных эвристиках:

- Файл в списке **«Что исправить»** (🟡).
- Рекомендация требует ручной проверки перед merge, без «можно мержить».
- Отказ виден как предупреждение о незавершённой проверке языка и стиля.

## 13. Запрещённая инфраструктура

В конвейере не должно быть:

- частичной сборки нового английского файла из старого перевода без portable
  coverage plan, exact source/target hashes и независимо проверяемого
  доказательства каждой заменяемой границы;
- эвристики малой величины изменения, возраста, count/kind/position/LCS/fuzzy
  matching и недоказанной дифференциальной склейки;
- обязательных implementation или remediation manifests;
- policy gates, baseline snapshots и hash self-entry проверок, созданных только для работы агентов;
- зависимости runtime от папки протоколов агентов;
- бесконечных повторов;
- тихого `success`, если переводной pull request не создан.

Реальные manifests задания модели, защищённых атомов, списка файлов pull request и тестовых навигационных данных сохраняются, если они непосредственно нужны runtime или тестам продукта.

## 14. Приёмка

Перед публикацией версии должны быть проверены как минимум:

- старый слитый pull request с более новыми RU и EN файлами;
- текущий русский текст действительно передан модели и применён;
- предупреждение о более новых файлах не блокирует commit и push;
- жёсткий provenance-конфликт блокирует модель и публикацию;
- отсутствующий первоначальный RU блокирует все последующие этапы;
- исторически удалённый RU не удаляет актуальный EN, независимо от последующего восстановления RU;
- зависимость, которой нет в EN, переводится один раз;
- общий лимит дополнительных зависимостей равен 20;
- ASCII-якоря сохраняются, кириллические якоря согласованно локализуются в пределах job;
- ссылки, код, YAML, YFM, front matter и плейсхолдеры восстанавливаются без повреждений;
- `title`/`description` и YFM titles переводятся, остальной front matter и маркеры YFM защищены;
- новый EN из TOC-достижимого RU появляется в EN TOC в той же транзакции;
- технический повтор модели и переключение модели работают;
- локальное исправление критика ограничено двумя попытками;
- после успешного перевода создан pull request;
- `build-docs` и `doc_verify` зелёные на одном SHA либо явно зафиксирован статус ожидания CI без ложного success при отсутствии PR;
- полный набор unit-тестов не имеет падений.
- **R-GL-17:** exact certificate Subject atom локализуется после href restoration
  при одном или нескольких вхождениях и остаётся английским при повторной
  финализации; `en` и `english` одинаково блокируют русское написание на всех
  Markdown-путях scope #51079. Generic assignment/code, больший code-атом,
  fenced code и HTML comment сохраняются без fuzzy-переписывания.
- **R-GL-4:** modified diff page с pre-existing href к missing tip fragment не ставит owner в `doc_from_main`; new page с href к fragment уже на tip EN не ставит owner; new href на diff page к missing tip fragment ставит owner; translate batches все ≤ `batch_max_output_chars` estimate; oversized paragraph split на `\n\n`; нет overlapping batches; `finish_reason=length` на 2-segment batch → один resplit → success; irreducible monolith → `ManualAction`, не soft-keep.
- **R-GL-5:** настоящий harness с отказом модели на чистой прозе и трёх incident phrases даёт `warnings`, `_file_has_open_issues` = `True`, отчёт 🟡 с ручной проверкой. Mixed batches и Task 5 atom blockers остаются 🔴; повторный отказ и pair post-repair QA сохраняют предупреждение; `critic_execution_failed` остаётся 🔴; ASCII без сегментов не вызывает критика. Compatibility `warnings` с пустыми issues сохраняет жёлтый статус.
- **R-GL-6:** merged #40385 fixture — 6 пар (5 diff + `_includes/connect.md`); pre-existing `connect.md#tls` на modified `authentication.md` → `doc_from_main` содержит `_includes/connect.md`, `auth_config` — нет; после translate+declare `apply_en_link_target_checks` == `[]` на `authentication.md`; `test_pr_40385_real_tip_without_queued_translation_stays_blocked` остаётся блокирующим при bypass owner pair; declare fallback: synthetic aligned include → append `{#frag}`; real-tip misaligned без translate → fallback `None`.
- **R-GL-7/R-GL-8:** production/fixture #52077 при `candidate == en_baseline` разрешает доказанный legacy-translit `#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov`→`#enabling-the-node-authentication-and-authorization-mode` и блокирует ровно две подмены критика: `#certificate-auth-config`→`#iam-auth-config` и `#tls`→`#activated-profile`. Explicit, missing-target и ambiguous варианты остаются blocking; duplicate occurrences дают отдельные blockers; same-path ambient extra при наличии точного href не создаёт false positive; path-only redirect с тем же ASCII fragment проходит; URL-encoded кириллический fragment и dictionary localization проходят.
- **R-GL-9:** fragment-repair fixture сохраняет `#security-auth`/`#certificate-auth-config`/`#tls`, даже если baseline указывает на существующий другой explicit anchor; доказанный implicit heading slug по-прежнему может использовать локализованный baseline. Delete+add same-fragment и duplicate historical fragment не получают final path restore и остаются blocking.
- **R-GL-10:** #40385 scope fixture с pre-existing `auth_config#security-auth` (не объявлен в RU owner) и `auth_config#certificate-auth-config` (explicit в RU, отсутствует EN) ставит `auth_config.md` в `doc_from_main` из-за certificate fragment, не из-за broken security fragment. Source-valid current RU target не откатывается на tip EN path.
- **R-GL-11:** production-shaped #40385 fixture проходит полный post-translate lifecycle локально с 3 current RU links против 2 RU/EN baseline links: disabled reconciliation оставляет `en_link_target` blocker; final reconciliation сохраняет tip `security_config.md#security-auth`, declaration читает `auth_config.md` из source `ru_content_ref` и добавляет `{#certificate-auth-config}` byte-identically; финальный `apply_en_link_target_checks` возвращает `[]` без запуска GitHub Actions.
- **R-GL-12/R-GL-13:** top-level `run_doc_translate` tests на real temporary git tree доказывают, что safe final `en_link_target` blocker сохраняет `completeness_gaps == []`, не очищает touched paths, вызывает prepare/commit/push и `create_pull(draft=True)`, а blocker на declaration/redirect impact path без `PairRunResult` остаётся в `final_tree_blockers`. Production-shaped soft-keep fixture воспроизводит 8 clean translations + retained `authentication.md`: result даёт `Translated: 8`, `Retained for manual repair: 1`, draft `PUBLISH_RED`, exact published-bytes hash и durable manifest. Missing/new target, raw error, unsafe structure/critic и no-diff artifact остаются withheld; standalone verify сохраняет unchanged hash и снимает blocker только после changed green pair validation. Existing ready PR получает `convertPullRequestToDraft`; clean candidate остаётся `PUBLISH_NORMAL`.
- **R-GL-14:** production-shaped #52330 fixture доказывает единственный frozen-B wrapper proposal, точный one-file/one-line K→K2 diff, `+103` bytes и идемпотентность. Негативные случаи покрывают raw leave-and-return traversal, custom core root, query/fragment classification, `_` и Unicode Mn/Mc/Me на границах label, ambiguous/missing/unsafe target, отсутствие K fragment, typed report blockers, bypass repair application и bypass fresh recursive verify. Real-Git workflow создаёт один commit/push, подтверждает K2 parentage, пустой K2 apply и отсутствие второго push.
- **R-GL-15:** real-Git workflow воспроизводит stale PR head K после успешного push K2 и доказывает успех при двух и пяти stale reads, bounded failure после шести stale reads, точные waits `1,2,4,8,15`, отсутствие повторного push и model/apply/comment work до согласования. Отдельные controls немедленно блокируют третий SHA во время REST-read, изменение ref после handshake и удаление recursive equality gate. Неизменённые A05 evidence-drop/foreign-identity tests обязаны по-прежнему получать `ValueError`. Production acceptance требует final `doc_verify`, docs build и PR-check success на одном K2; локальные тесты или перемещение release tag этого не заменяют.
- **R-GL-16:** fixture #51079 с восемью Markdown и `security/toc_p.yaml` освобождает оба include-only `_assets/user-token*.md` до записи pending файлов на диск. Controls сохраняют orphan для include из недостижимого referrer, ordinary link, fenced/code/comment/front-matter example, missing/deleted/errored target, cross-locale и raw leave-and-return пути. Обязательны отдельные RED→GREEN tests с настоящим TAB-indented example и YAML block scalar. Проверяются nested includes, завершение циклов, пустой существующий fragment, authoritative empty/replaced pending text и отсутствие fallback к dirty worktree. Existing include-target blocker и независимые QA/publication blockers сохраняются. Production acceptance требует нового translation PR, независимой проверки содержания, зелёных `doc_verify` и build на одном SHA.

## 15. Работа команды

- Для каждой новой задачи создаются свежие аналитик, внешний reviewer, разработчик и тестер.
- Аналитик формулирует одно конкретное решение и не оставляет разработчику архитектурных выборов.
- Внешний reviewer проверяет спецификацию до реализации.
- Разработчик реализует требования буквально. При неоднозначности он останавливается и задаёт вопрос аналитику.
- Тестер проверяет конкретные требования и полный пользовательский сценарий, а не только отдельные функции.
- Внешний reviewer проверяет итоговую реализацию и тесты.
- Диспетчер сам следит за этапами, немедленно передаёт работу дальше и продолжает цикл до результата или явной команды `стоп`.
