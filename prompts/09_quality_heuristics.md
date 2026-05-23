# Эвристические проверки качества перевода

Этот файл — конфиг для модуля `ydbdoc_review.heuristics`. Каждый блок ниже описывает одну проверку. Чтобы добавить новую проверку — допишите блок в этот файл по тому же шаблону.

Поля каждого блока:

- `name` — машинное имя проверки. Если Python реализовал чекер с таким именем, он вызывается; иначе проверка отправляется в LLM с этим описанием.
- `severity` — `warning` или `critical`. `critical` означает «скорее всего что-то реально сломано»; `warning` — «обратите внимание».
- `applies_to` — для какого направления перевода применяется: `ru_to_en`, `en_to_ru` или `any`.
- `description` — что именно проверяем, простыми словами. Это же описание уйдёт в LLM для проверок, у которых нет детерминированной реализации.
- `report_message` — шаблон сообщения для отчёта. Может содержать плейсхолдеры `{location}`, `{detail}`.

---

```yaml
name: cyrillic_in_en
severity: warning
applies_to: ru_to_en
description: |
  В EN-файле перевода не должно остаться кириллических букв ни в одном месте:
  ни в prose, ни в таблицах, ни в комментариях кода, ни в строковых литералах.
  Допустимы только идентификаторы и имена собственные на русском, если они
  обрамлены в кавычки или backticks (например, `` `имя_таблицы` ``).
  Латинские буквы в комментариях кода **русского** файла — допустимы и
  никогда не флагируются.
report_message: |
  В EN остались кириллические буквы — найдено {detail}. Проверьте {location}.
```

```yaml
name: file_length_mismatch
severity: critical
applies_to: any
description: |
  Соотношение длин SOURCE и TRANSLATION не должно расходиться более чем на 25%.
  Считаем по `|len(EN) - len(RU)| / max(len(RU), 1)`. Расхождение больше порога
  обычно означает потерянный раздел, выкинутый блок кода или галлюцинацию
  переводчика, который дописал лишнее.
report_message: |
  Длины SOURCE и TRANSLATION различаются на {detail} (порог 25%).
```

```yaml
name: heading_count_mismatch
severity: critical
applies_to: any
description: |
  Число заголовков `##` (h2) и `###` (h3) в TRANSLATION должно совпадать с SOURCE.
  Расхождение означает потерянный или дублированный раздел.
report_message: |
  Несоответствие числа заголовков: {detail}.
```

```yaml
name: fence_unbalanced
severity: critical
applies_to: any
description: |
  В TRANSLATION число строк, начинающихся с тройного бэктика (открытие/закрытие
  fence-блока кода), должно быть чётным. Нечётное число → есть незакрытый блок,
  файл сломан.
report_message: |
  Не закрыт fence-блок: число `` ``` `` в файле нечётное ({detail}).
```

```yaml
name: list_tabs_mismatch
severity: critical
applies_to: any
description: |
  Число блоков `{% list tabs %}` и набор вкладок внутри них должны совпадать
  между SOURCE и TRANSLATION. Имена вкладок (`- Go`, `- Python`, `- Java`)
  не переводятся.
report_message: |
  Несоответствие SDK-вкладок: {detail}.
```

```yaml
name: section_untranslated
severity: critical
applies_to: ru_to_en
description: |
  Целая секция (от `## ` или `### ` до следующего заголовка того же уровня)
  в TRANSLATION-файле осталась на языке SOURCE — например, несколько подряд
  абзацев, полностью на русском. Отдельные кириллические слова или имена
  собственные — не критично (для этого есть warning `cyrillic_in_en`).
  Эту проверку делает LLM: она сравнивает посекционно две версии и определяет,
  не пропустил ли переводчик целую секцию.
report_message: |
  В TRANSLATION секция «{location}» не переведена.
```

```yaml
name: liquid_tags_balance
severity: critical
applies_to: any
description: |
  Парные Diplodoc-теги в TRANSLATION должны быть закрыты: число открытий
  `{% note ... %}` равно числу `{% endnote %}`, аналогично для `{% cut %}` ↔
  `{% endcut %}` и `{% list tabs %}` ↔ `{% endlist %}`.
report_message: |
  Не сбалансированы Diplodoc-теги: {detail}.
```

```yaml
name: wikipedia_ru_in_en
severity: critical
applies_to: ru_to_en
description: |
  В EN-переводе не должно быть ссылок на ru.wikipedia.org и кириллицы в slug
  Wikipedia-URL (например, Snappy_(библиотека)). Допустим только en.wikipedia.org
  с латинским slug.
report_message: |
  В EN остались русскоязычные Wikipedia-ссылки: {detail}.
```

```yaml
name: broken_markdown_link
severity: critical
applies_to: ru_to_en
description: |
  В EN не должно быть сломанных markdown-ссылок: голый URL в скобках `(https://...)`
  вместо `[текст](url)`, пустой `[#anchor]()`, `[text]()` без href.
report_message: |
  Сломанные markdown-ссылки в EN: {detail}.
```

```yaml
name: heading_anchor_mismatch
severity: critical
applies_to: any
description: |
  Якоря `{#...}` у заголовков `##`/`###` в TRANSLATION должны совпадать с SOURCE
  по порядку (тот же id на той же позиции среди заголовков с якорями).
report_message: |
  Несовпадение якорей заголовков: {detail}.
```

```yaml
name: table_checkmark_drift
severity: critical
applies_to: any
description: |
  В таблицах с галочками `✓` (типы данных, алгоритмы сжатия) позиции галочек
  в каждой строке должны совпадать с SOURCE. Имена типов и ✓ не сдвигаются
  между столбцами при переводе. Сравнивайте строки внутри одной таблицы
  (чтение и запись — отдельно).
report_message: |
  Галочки ✓ в таблице не совпадают с SOURCE. {detail}
```
