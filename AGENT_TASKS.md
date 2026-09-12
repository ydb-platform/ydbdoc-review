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

---

## ЗАДАНИЕ 3: Differential Translation System

### Проблема
Каждый раз переводим ВЕСЬ EN файл с нуля, даже если он был правильно переведен вчера. Огромная трата ресурсов.

**Текущая система:**
```
RU PR (изменение) → Full EN translation (ВЕСЬ файл) → Critic → Deploy
```

**Желаемая система:**
```
RU diff (что изменилось) → Translate ONLY changes → Merge with existing EN → Critic → Deploy
```

### Требование 1: Decision Tree (Full vs Differential)

```python
@dataclass
class TranslationStrategy:
    mode: Literal["full", "differential", "skip"]
    reason: str
    config: dict

def analyze_file_state(
    ru_pr_path: str,
    en_current_path: str | None,
    ru_base_path: str,
    repo_path: str,
) -> TranslationStrategy:
    """
    Decision tree:
    
    EN file exists?
      ├─ NO → mode="full" (новый файл, переводим с нуля)
      └─ YES:
         ├─ EN file is stale (>90 дней)?
         │  └─ YES → mode="full" (EN старый, переводим заново)
         │
         ├─ EN file is incomplete (<30% RU размера)?
         │  └─ YES → mode="full" (EN скелет, переводим целиком)
         │
         └─ Change magnitude > 50%?
            ├─ YES → mode="full" (слишком много изменений)
            └─ NO → mode="differential" (переводим только changes)
    """
```

### Требование 2: RuDiffAnalysis (block-level diff)

```python
@dataclass
class RuDiffAnalysis:
    change_type: Literal["new_file", "deleted_file", "modified"]
    added_blocks: list[TextBlock]
    modified_blocks: list[TextBlock]
    removed_blocks: list[TextBlock]
    change_magnitude: float  # 0.0 - 1.0 (% changed)

@dataclass
class TextBlock:
    kind: str  # "section", "paragraph", "list", "code", "table", "note"
    content: str
    line_range: tuple[int, int]
    heading_level: int | None = None

def parse_markdown_blocks(text: str) -> list[TextBlock]:
    """Парсить markdown на блоки (section, para, code, etc)."""

def analyze_ru_diff(
    ru_base_path: str,
    ru_pr_path: str,
    repo_path: str,
) -> RuDiffAnalysis:
    """Анализ изменений между RU base и RU PR."""
```

### Требование 3: DifferentialTranslationPlan

```python
@dataclass
class DifferentialTranslationPlan:
    added_blocks: list[TextBlock]        # Новые блоки
    modified_blocks: list[TextBlock]     # Измененные блоки
    en_blocks_to_keep: list[TextBlock]   # Старые EN блоки оставляем
    merge_strategy: Literal["reconstruct", "patch"]

def plan_differential_translation(
    ru_pr_path: str,
    en_current_path: str,
    ru_base_path: str,
    ru_diff_analysis: RuDiffAnalysis,
    repo_path: str,
) -> DifferentialTranslationPlan:
    """
    Спланировать какие блоки переводить.
    
    1. Определить какие RU блоки добавлены/изменены
    2. Определить какие EN блоки соответствуют неизменным RU блокам
    3. Вернуть план: что переводить, что оставить
    """

def find_corresponding_en_block(
    ru_block: TextBlock,
    ru_base_blocks: list[TextBlock],
    en_blocks: list[TextBlock],
) -> TextBlock | None:
    """
    Найти соответствующий EN блок для RU блока.
    
    Стратегия:
    1. Exact heading match (если заголовок одинаков)
    2. Positional heuristic (блок на той же позиции)
    3. Content similarity (fuzzy match на начало)
    """
```

### Требование 4: DifferentialTranslationExecutor

```python
class DifferentialTranslationExecutor:
    def translate_differential(
        self,
        plan: DifferentialTranslationPlan,
        translator: Callable,  # LLM translator
    ) -> str:
        """
        Выполнить differential translation:
        1. Translate added_blocks
        2. Translate modified_blocks
        3. Merge с kept en_blocks в правильном порядке
        4. Return итоговый EN текст
        """
        
    def merge_translations(
        self,
        translated_added: list[str],
        translated_modified: list[str],
        kept_en_blocks: list[str],
        original_ru_structure: list[TextBlock],
    ) -> str:
        """Мерджить переводы сохраняя структуру RU PR."""
```

### Требование 5: Integration с текущей pipeline

```python
def translate_file_smart(
    ru_pr_path: str,
    ru_base_path: str,
    en_current_path: str | None,
    repo_path: str,
    translator: Callable,
) -> str | None:
    """
    Smart translation (full или differential).
    
    Заменяет текущую:
        full_translate(ru_pr_text, target_lang="en")
    """
    analyzer = DifferentialTranslationAnalyzer()
    strategy = analyzer.analyze_file_state(...)
    
    if strategy.mode == "skip":
        return None
    elif strategy.mode == "full":
        ru_text = read_file(repo_path, ru_pr_path)
        return full_translate(ru_text, target_lang="en")
    elif strategy.mode == "differential":
        plan = analyzer.plan_differential_translation(...)
        executor = DifferentialTranslationExecutor()
        return executor.translate_differential(plan, translator)
```

### Требование 6: Edge Cases

**Case 1: EN file too small**
- EN size: 50 bytes, RU size: 5000 bytes
- Decision: FULL (EN is skeleton/stub)
- Config: `min_en_file_ratio = 0.3` (EN must be >30% of RU)

**Case 2: EN file stale**
- Last modified: 6 месяцев назад
- Decision: FULL (may be outdated)
- Config: `stale_days_threshold = 90`

**Case 3: Small change**
- RU diff: 10% новых строк
- Decision: DIFFERENTIAL (low risk)
- Plan: translate only new sections, keep old EN

**Case 4: Large restructure**
- RU diff: 60% изменений
- Decision: FULL (safer to retranslate)
- Reason: high change_magnitude_threshold = 0.5 (50%)

**Case 5: New file**
- EN: doesn't exist
- RU PR: добавил новый файл
- Decision: FULL (create from scratch)

**Case 6: EN was deleted**
- EN base: exists
- EN current: doesn't exist (удалили в main)
- RU PR: adds file back
- Decision: FULL (with advisory warning)

### Requirement 7: Configuration

```python
@dataclass
class DifferentialTranslationConfig:
    stale_days_threshold: int = 90         # EN старше = full translate
    change_magnitude_threshold: float = 0.5 # >50% changes = full translate
    min_en_file_ratio: float = 0.3         # EN size < 30% RU = full translate
    enable_fuzzy_matching: bool = True
    fuzzy_match_threshold: float = 0.8
```

### Requirement 8: Logging & Metrics

```python
# Логировать decision tree path:
logger.info("File %s: EN exists (%.0f days old, %.1f%% of RU)",
            en_path, age_days, en_to_ru_ratio)
logger.info("Change magnitude: %.1f%% → decision: %s",
            change_magnitude * 100, strategy.mode)

# Metrics (для CI dashboard):
# - ratio of differential vs full translations
# - estimated LLM token savings
# - block match success rate (fuzzy matching)
```

### Acceptance Criteria
1. ✅ `DifferentialTranslationAnalyzer` корректно определяет mode (full/differential/skip)
2. ✅ `RuDiffAnalysis` точно вычисляет added/modified/removed blocks
3. ✅ `DifferentialTranslationPlan` правильно планирует какие блоки переводить
4. ✅ Все 6 edge cases обработаны и протестированы
5. ✅ Fuzzy matching между RU и EN блоками работает (8+ тестов)
6. ✅ Мерджа переводов сохраняет структуру и порядок
7. ✅ Integration: типичный PR (добавление параграфа) использует differential, не full
8. ✅ Metrics логируются (token savings, decision ratios)
9. ✅ Все существующие тесты проходят
10. ✅ Configuration параметры настраиваются (не хардкодированы)

### Expected Benefits

| Метрика | До | После |
|---------|----|----|
| **LLM токены на PR** | 100% | ~20-30% (для типичных PR) |
| **Время обработки** | 100% | ~30-40% |
| **Regression risk** | Высокий | Низкий |
| **Critic workload** | 100% | ~20-30% |

---

## Что НЕ надо делать

- ❌ Большой persistent cache между сборками
- ❌ Генерировать offline таблицы из JSON
- ❌ Менять публичные API функций
- ❌ Удалять старый код (рефакторить gradually)
