# Задание: Differential Translation System (Incremental instead of Full Rewrite)

## Контекст и Проблема

**Текущая система:** Каждый раз полностью переводим EN документ с нуля
```
RU PR (изменение) → Full EN translation → Critic review → Deploy
```

**Проблема:**
- 🔴 Колоссальная трата ресурсов (LLM токены, время)
- 🔴 Высокий риск регрессии (переводим уже работающий текст → может сломаться)
- 🔴 Critic тратит время на проверку того, что вчера уже было правильно
- 🔴 При каждом PR возрастает вероятность новых багов в переводе

**Желаемая система:** Differential (incremental) translation
```
RU diff (что изменилось) → Translate ONLY changes → Merge with existing EN → Critic review → Deploy
```

**Выгода:**
- ✅ Сокращение LLM токенов в 3-10 раз (для большинства PR)
- ✅ Меньше риск регрессии (не трогаем работающие части)
- ✅ Быстрее обработка PR
- ✅ Стабильнее переводы (меньше переводов = меньше ошибок)

---

## Алгоритм: Differential Translation Decision Tree

### Шаг 0: Получить информацию о файле

```python
def analyze_translation_state(
    ru_pr_path: str,          # Путь в RU PR
    en_current_path: str,     # Путь в EN (может не существовать)
    ru_base_path: str,        # Путь в base RU (для diff)
    repo_path: str,
) -> TranslationStrategy:
    """
    Определить стратегию перевода файла.
    
    Returns:
        TranslationStrategy(
            mode: Literal["full", "differential", "skip"],
            reason: str,
            config: {...}
        )
    """
```

### Шаг 1: Существует ли EN версия?

```
EN file exists?
  ├─ NO → Перейти к Шагу 2 (новый файл в PR)
  └─ YES → Перейти к Шагу 3 (обновление существующего)
```

#### **Шаг 1.1: EN файл НЕ существует**

```python
# EN файл не существует, но может быть в base (был удален)?
if en_base_exists and not en_current_exists:
    # EN был удален или не создан
    return TranslationStrategy(
        mode="full",  # Переводим файл с нуля
        reason="EN file does not exist; will create from RU PR",
        config={"is_new_file": True}
    )

if not en_base_exists and not en_current_exists:
    # Совсем новый файл
    return TranslationStrategy(
        mode="full",
        reason="New file in RU PR, creating EN from scratch",
        config={"is_new_file": True}
    )
```

#### **Шаг 1.2: EN файл СУЩЕСТВУЕТ → Перейти к Шагу 3**

### Шаг 2: Проанализировать diff в RU

```python
def analyze_ru_diff(
    ru_base_path: str,
    ru_pr_path: str,
    repo_path: str,
) -> RuDiffAnalysis:
    """Анализ изменений в RU файле."""
    base_text = read_file(repo_path, ru_base_path)
    pr_text = read_file(repo_path, ru_pr_path)
    
    if base_text is None:
        return RuDiffAnalysis(
            change_type="new_file",
            added_blocks=[],
            modified_blocks=[],
            ...
        )
    
    # Compute diff
    diff = compute_diff(base_text, pr_text)
    
    return RuDiffAnalysis(
        change_type="modified",
        added_blocks=extract_added_blocks(diff),
        modified_blocks=extract_modified_blocks(diff),
        removed_blocks=extract_removed_blocks(diff),
        change_magnitude=len(diff) / len(base_text),
    )
```

### Шаг 3: Решить — Full или Differential?

```
EN file exists? YES
  ├─ Change magnitude > 50%? 
  │  ├─ YES → FULL translation (слишком много изменений)
  │  └─ NO → Перейти к Шагу 4
  └─ EN file is "too old"?
     ├─ YES (last_commit > 90 дней) → FULL translation
     └─ NO → Перейти к Шагу 4
```

#### **Определение "слишком старый":**

```python
def is_en_file_stale(
    en_path: str,
    repo_path: str,
    stale_days: int = 90,
) -> bool:
    """Проверить, не слишком ли старый EN файл."""
    # Найти последний commit, который изменил en_path
    last_modified = get_last_commit_date(repo_path, en_path)
    age_days = (datetime.now() - last_modified).days
    
    if age_days > stale_days:
        logger.info(
            "EN file %s is stale (last modified %d days ago)",
            en_path, age_days
        )
        return True
    return False
```

#### **Определение "слишком много изменений":**

```python
def is_change_magnitude_high(
    ru_diff_analysis: RuDiffAnalysis,
    threshold: float = 0.5,  # 50%
) -> bool:
    """Если более 50% текста изменилось — проще перевести заново."""
    magnitude = ru_diff_analysis.change_magnitude
    if magnitude > threshold:
        logger.info(
            "Change magnitude %.1f%% > threshold %.1f%% — use FULL translation",
            magnitude * 100, threshold * 100
        )
        return True
    return False
```

### Шаг 4: Differential Translation Plan

```python
def plan_differential_translation(
    ru_pr_path: str,
    en_current_path: str,
    ru_base_path: str,
    ru_diff_analysis: RuDiffAnalysis,
    repo_path: str,
) -> DifferentialTranslationPlan:
    """
    Спланировать какие блоки переводить.
    
    Returns:
        DifferentialTranslationPlan(
            added_blocks: list[TextBlock],    # Новые блоки в RU PR
            modified_blocks: list[TextBlock], # Измененные блоки
            en_blocks_to_keep: list[TextBlock], # Старые EN блоки, которые оставляем
            merge_strategy: str,  # Как мерджить
        )
    """
    
    ru_pr_text = read_file(repo_path, ru_pr_path)
    en_current_text = read_file(repo_path, en_current_path)
    ru_base_text = read_file(repo_path, ru_base_path)
    
    # Парсить структуру (markdown sections, code blocks, etc.)
    ru_pr_blocks = parse_markdown_blocks(ru_pr_text)
    en_blocks = parse_markdown_blocks(en_current_text)
    ru_base_blocks = parse_markdown_blocks(ru_base_text)
    
    # Определить какие блоки добавлены/изменены
    added_blocks = identify_added_blocks(ru_base_blocks, ru_pr_blocks)
    modified_blocks = identify_modified_blocks(ru_base_blocks, ru_pr_blocks)
    
    # Определить какие EN блоки соответствуют RU и оставить их
    en_blocks_to_keep = []
    for ru_block in ru_pr_blocks:
        if ru_block not in added_blocks and ru_block not in modified_blocks:
            # Этот блок не изменился в RU
            corresponding_en = find_corresponding_en_block(
                ru_block, ru_base_blocks, en_blocks
            )
            if corresponding_en:
                en_blocks_to_keep.append(corresponding_en)
    
    return DifferentialTranslationPlan(
        added_blocks=added_blocks,
        modified_blocks=modified_blocks,
        en_blocks_to_keep=en_blocks_to_keep,
        merge_strategy="reconstruct",  # Или "patch"
    )
```

---

## Основные Компоненты Системы

### 1. **DifferentialTranslationAnalyzer**

```python
@dataclass
class TranslationStrategy:
    mode: Literal["full", "differential", "skip"]
    reason: str
    config: dict

@dataclass
class RuDiffAnalysis:
    change_type: Literal["new_file", "deleted_file", "modified"]
    added_blocks: list[TextBlock]
    modified_blocks: list[TextBlock]
    removed_blocks: list[TextBlock]
    change_magnitude: float  # 0.0 - 1.0

@dataclass
class DifferentialTranslationPlan:
    added_blocks: list[TextBlock]
    modified_blocks: list[TextBlock]
    en_blocks_to_keep: list[TextBlock]
    merge_strategy: Literal["reconstruct", "patch"]

class DifferentialTranslationAnalyzer:
    def analyze_file_state(
        self,
        ru_pr_path: str,
        en_current_path: str | None,
        ru_base_path: str,
        repo_path: str,
    ) -> TranslationStrategy:
        """Определить стратегию перевода."""
        
    def plan_translation(
        self,
        ru_pr_path: str,
        en_current_path: str,
        ru_base_path: str,
        repo_path: str,
    ) -> DifferentialTranslationPlan:
        """Спланировать differential translation."""
```

### 2. **DifferentialTranslationExecutor**

```python
class DifferentialTranslationExecutor:
    def translate_differential(
        self,
        plan: DifferentialTranslationPlan,
        translator: Callable,  # LLM translator function
    ) -> str:
        """
        Выполнить differential translation:
        1. Перевести added_blocks
        2. Перевести modified_blocks
        3. Оставить en_blocks_to_keep как есть
        4. Мерджить всё вместе
        5. Вернуть итоговый EN текст
        """
        
    def merge_translations(
        self,
        translated_added: list[str],
        translated_modified: list[str],
        kept_en_blocks: list[str],
        original_ru_structure: list[TextBlock],
        merge_strategy: str,
    ) -> str:
        """Мерджить переводы в правильном порядке."""
```

### 3. **Интеграция с текущей pipeline**

```python
def translate_file_smart(
    ru_pr_path: str,
    ru_base_path: str,
    en_current_path: str,
    repo_path: str,
    translator: Callable,
) -> str | None:
    """
    Intelligently translate file (full or differential).
    
    Used by existing translation pipeline instead of:
        full_translate(ru_pr_text, target_lang="en")
    """
    analyzer = DifferentialTranslationAnalyzer()
    strategy = analyzer.analyze_file_state(
        ru_pr_path, en_current_path, ru_base_path, repo_path
    )
    
    if strategy.mode == "skip":
        return None
    
    if strategy.mode == "full":
        ru_text = read_file(repo_path, ru_pr_path)
        return full_translate(ru_text, target_lang="en")
    
    if strategy.mode == "differential":
        plan = analyzer.plan_translation(
            ru_pr_path, en_current_path, ru_base_path, repo_path
        )
        executor = DifferentialTranslationExecutor()
        return executor.translate_differential(plan, translator)
```

---

## Edge Cases

### Case 1: EN файл существует, но очень старый

```
Last modified: 6 месяцев назад
Decision: FULL translation (слишком стар, вероятно outdated)
Reason: "EN file is stale (>90 days), will do full retranslation"
```

### Case 2: EN файл существует, но содержит только 2-3 строки (skeleton)

```
EN file size: 50 bytes
RU PR file size: 5000 bytes
Decision: FULL translation (EN файл не полный)
Reason: "EN file too small (~1% of RU), likely incomplete; full translation"
Config: { "en_file_incomplete": True, "en_to_ru_ratio": 0.01 }
```

### Case 3: RU PR добавил 10% новых строк

```
Change magnitude: 0.10 (10%)
Decision: DIFFERENTIAL translation
Reason: "Low change magnitude (10%); translate only added sections"
Plan:
  - added_blocks: [section about new feature]
  - modified_blocks: []
  - en_blocks_to_keep: [all existing sections]
```

### Case 4: RU PR переструктурировал 60% документа

```
Change magnitude: 0.60 (60%)
Decision: FULL translation
Reason: "High change magnitude (60%); restructuring detected; full translation safer"
```

### Case 5: EN файл не существует, RU PR добавил новый файл

```
EN file state: doesn't exist (not in base, not in current)
Decision: FULL translation
Reason: "New file in RU PR; will create EN from scratch"
Config: { "is_new_file": True }
```

### Case 6: EN файл удален в current, но RU PR добавил его обратно

```
EN base: exists (в последнем released)
EN current: doesn't exist (удалили в main)
RU PR: добавил файл
Decision: FULL translation (с warning)
Reason: "EN file was deleted in main but RU PR adds it back; full translation with caution"
Config: { "en_was_deleted": True, "advisory": "check if deletion was intentional" }
```

---

## Требования к Реализации

### 1. **Block-level diff для markdown**

```python
def parse_markdown_blocks(text: str) -> list[TextBlock]:
    """
    Парсить markdown на блоки (section, paragraph, code block, list, etc).
    
    Returns:
        [
            TextBlock(kind="section", content="# Заголовок", line_range=(0, 1)),
            TextBlock(kind="paragraph", content="Текст параграфа", line_range=(2, 10)),
            TextBlock(kind="code", content="```\\ncode\\n```", line_range=(11, 15)),
            ...
        ]
    """

@dataclass
class TextBlock:
    kind: str  # "section", "paragraph", "list", "code", "table", "note", etc
    content: str
    line_range: tuple[int, int]  # (start_line, end_line)
    heading_level: int | None = None
```

### 2. **Mapping между RU и EN блоками**

```python
def find_corresponding_en_block(
    ru_block: TextBlock,
    ru_base_blocks: list[TextBlock],
    en_blocks: list[TextBlock],
    fuzzy_match_threshold: float = 0.8,
) -> TextBlock | None:
    """
    Найти соответствующий EN блок для RU блока.
    
    Стратегия:
    1. Exact heading match (если заголовок "## Установка" в обоих)
    2. Positional heuristic (блок на той же позиции в структуре)
    3. Content similarity (fuzzy match на начало блока)
    """
```

### 3. **Стратегия мерджа**

```python
merge_strategy = "reconstruct"  # или "patch"

# "reconstruct": Собрать EN из частей
#   - Translate added
#   - Translate modified
#   - Keep existing EN blocks
#   - Combine в порядке RU PR structure

# "patch": Apply changes на существующий EN
#   - Diff RU base → RU PR
#   - Apply same diff на EN (но на EN эквиваленте)
#   - (более сложно, но может работать для minor changes)
```

### 4. **Configuration и Thresholds**

```python
@dataclass
class DifferentialTranslationConfig:
    stale_days_threshold: int = 90  # EN старше 90 дней = full translate
    change_magnitude_threshold: float = 0.5  # >50% changes = full translate
    min_en_file_ratio: float = 0.3  # EN размер < 30% RU размера = full translate
    enable_fuzzy_matching: bool = True
    fuzzy_match_threshold: float = 0.8
```

---

## Acceptance Criteria

1. ✅ `DifferentialTranslationAnalyzer` корректно определяет mode (full/differential/skip)
2. ✅ `RuDiffAnalysis` точно вычисляет added/modified/removed blocks
3. ✅ `DifferentialTranslationPlan` правильно планирует какие блоки переводить
4. ✅ Обработаны все 6+ edge cases (stale EN, small EN, high magnitude, etc)
5. ✅ Fuzzy matching между RU и EN блоками работает (8+ тестов)
6. ✅ Мерджа переводов сохраняет структуру (reconstruct strategy)
7. ✅ Integration tests: для типичных PR (добавление параграфа, изменение раздела) — differential работает
8. ✅ Metrics: логирование decision tree (full vs differential ratio, LLM token savings)
9. ✅ Все существующие тесты проходят
10. ✅ Configuration параметры легко настраиваются

---

## Benefits

| Метрика | До | После |
|---------|----|----|
| **LLM токены на PR** | 100% | ~20-30% (для типичного PR) |
| **Время обработки** | 100% | ~30-40% (меньше LLM запросы) |
| **Risk регрессии** | Высокий | Низкий (не трогаем работающие части) |
| **Critic workload** | Проверяет ВСЁ | Проверяет только changes |
| **Стабильность** | Нестабильно | Стабильнее (меньше переводов) |
