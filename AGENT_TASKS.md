# Задания для агента: Улучшение Wikipedia ссылок и TOC логики

---

## ЗАДАНИЕ 1: Stabilize Wikipedia Link Resolution

### Проблема
Модуль `wikipedia_links.py` непредсказуемо разрешает ссылки. При сетевых сбоях (TLS flakiness в CI) или недостатке offline данных — ссылки либо остаются на RU, либо теряют фрагменты.

### Требование 1: Retry logic с graceful degradation
- Текущий код одну ошибку → зависает на оригинальном href
- Нужна чёткая цепочка:
  1. Try: `_fetch_langlink()` (MediaWiki, timeout 5s)
  2. Try: `_fetch_wikidata_sitelink()` (Wikidata, timeout 5s)  
  3. Try: `_OFFLINE_EN_TITLES` lookup
  4. Return `None` (не href — дать обработать выше)
- На каждый retry: логировать WARNING с причиной

### Требование 2: Расширить offline dictionary вручную
- Текущая `_OFFLINE_EN_TITLES`: только 15 статей
- Добавить 50-100 популярных на основе git истории ydb-docs
- Пример: `("ru", "PostgreSQL"): "PostgreSQL"`, `("ru", "Apache Kafka"): "Apache Kafka"`
- Не генерировать из JSON — вручную добавить в код

### Требование 3: Явная валидация источника языка
- Вместо неявной логики `if source_lang == "en" and CYRILLIC.search(title):`
- Добавить функцию `_is_valid_wikipedia_url(href, wiki_lang, title) -> bool`
- Проверить формат URL, не пустой title, правильную кодировку

### Требование 4: Обработка фрагментов — маппирование вместо удаления
- Текущий код: Cyrillic фрагмент → просто удалить (потеря информации)
- Нужно: логировать WARNING при удалении неизвестного фрагмента
- Расширить `_OFFLINE_EN_FRAGMENTS` на основе ошибок в истории

### Требование 5: Тестирование
- ✅ Успешное разрешение (API работает)
- ✅ Fallback на Wikidata (MediaWiki пусто → Wikidata OK)
- ✅ Fallback на offline dict
- ✅ Return None при полном падении
- ✅ Обработка Cyrillic фрагментов
- ✅ URL без фрагмента

### Acceptance Criteria
1. `resolve_wikipedia_href()` при ошибке → None (не исходный href)
2. Retry-логика протестирована (mock requests, simulate timeouts)
3. `_OFFLINE_EN_TITLES` расширен до 50-100 записей
4. WARNING логи при удалении неизвестных фрагментов
5. Все существующие тесты проходят

---

## ЗАДАНИЕ 2: Refactor TOC Merge Logic

### Проблема
Логика мерджа TOC раздроблена и хрупка: 3 разные функции, brittle href matching, двойная обработка href vs include_path, двойной парсер для inline vs block.

### Требование 1: Unified TocMergeScope
- Текущий код: `toc_translate_scope()` вычисляет scope, `merge_en_toc_yaml()` применяет, `validate_toc_merge()` проверяет
- Нужна единая dataclass, которая явно описывает ALL изменения:

```python
@dataclass(frozen=True)
class TocMergeScope:
    """Что надо изменить в EN toc на основе RU PR."""
    added_hrefs: frozenset[str]        # Новые href в RU PR
    modified_hrefs: frozenset[str]     # href с изменённым name
    added_includes: frozenset[str]     # Новые include.path
    modified_includes: frozenset[str]  # include.path с изменённым name
    removed_hrefs: frozenset[str]      # Удалены из RU PR
    removed_includes: frozenset[str]   # Удалены из RU PR
```

### Требование 2: Explicit href mapping
- Текущий код: exact match `href in en_by_href` или die
- Если href переименован → не найдется EN соответствие
- Legacy aliases (`hive_config.md` vs `hive.md`) → специальная логика `_en_covers_ru_href()`
- Нужна явная таблица:

```python
@dataclass(frozen=True)
class TocEntryMapping:
    """Соответствие между RU и EN записями toc."""
    ru_href: str
    en_href: str
    en_name: str
    legacy_aliases: frozenset[str] = field(default_factory=frozenset)
```

### Требование 3: Unified TocItem модель
- Вместо раздельной логики для href и include_path
- Единая модель:

```python
@dataclass
class TocItem:
    """Единый элемент toc с поддержкой разных типов целей."""
    name: str
    target: str | None = None           # href или include.path
    target_kind: Literal["href", "include"] = "href"
    secondary_target: str | None = None # Для section entries (href + include)
    children: list[TocItem] = field(default_factory=list)
```

### Требование 4: Format-agnostic parser
- Текущий код: `_parse_toc_items_inline()` и `_parse_toc_items_block()` дублируют логику
- Нужен unified парсер → AST (list[TocItem]) + selective renderer

```python
class TocAstParser:
    """Парсит оба формата → TocItem AST."""
    def parse(yaml_text: str) -> list[TocItem]

class TocAstRenderer:
    """Сериализует TocItem AST → YAML (block или inline)."""
    def render(items: list[TocItem], format: str = "block") -> str
```

### Требование 5: Strict validation с categorization
- Текущая валидация: много WARNING'ов, неясная severity
- Нужна явная categorization:

```python
@dataclass(frozen=True)
class TocMergeIssue:
    kind: Literal[
        "scope_not_applied",      # ERROR — обязательно надо применить
        "orphan_ru_entry",        # ERROR — RU есть, EN нет (в scope)
        "orphan_en_entry",        # WARNING — EN есть, RU нет (legacy)
        "missing_href_target",    # BLOCKING — href на несуществующий файл
        "href_mismatch",          # WARNING — href переименован
        "structure_mismatch",     # WARNING — разная структура nested items
    ]
    severity: Literal["INFO", "WARNING", "ERROR", "BLOCKING"]
    detail: str
```

### Требование 6: Explicit merge strategy
- Нынешний merge order неясный
- Нужно явно определить:

```python
def merge_toc_yaml_v2(
    en_main: list[TocItem],
    ru_pr: list[TocItem],
    scope: TocMergeScope,
    mapping: list[TocEntryMapping],
) -> tuple[list[TocItem], list[TocMergeIssue]]:
    """
    Алгоритм:
    1. Идём по RU (сохраняем RU структуру)
    2. Для каждого RU item:
       - Если в scope → переводим name, используем RU структуру
       - Если НЕ в scope → берём EN блок полностью (если есть)
       - Если в mapping → используем mapped EN name
    3. Добавляем EN orphan'ы (не в RU PR):
       - Если orphan в en_main → append как есть
       - Если orphan новый → SKIP + WARNING
    4. Return: merged items + список issues
    """
```

### Acceptance Criteria
1. `TocMergeScope` dataclass явно описывает ALL изменения
2. `TocEntryMapping` таблица для legacy aliases (вместо `_en_covers_ru_href()` хардкода)
3. Unified `TocItem` модель для href и include_path
4. Format-agnostic парсер (один парсер → AST, один рендерер → YAML)
5. Strict validation с явной categorization severity
6. Merge strategy явно задокументирована в код и docstring
7. Все существующие тесты проходят
8. Новые тесты покрывают edge cases: nested items, section entries (href + include), legacy aliases

---

## Что НЕ надо делать

- ❌ Большой persistent cache между сборками
- ❌ Генерировать offline таблицы из JSON
- ❌ Менять публичные API функций
- ❌ Удалять старый код (рефакторить gradually)
