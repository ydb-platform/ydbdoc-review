# Translation pipeline

Единственный путь для `doc_translate` и `doc_verify`. Никаких legacy-веток.

## doc_translate

1. **Analyze** — модель из `[models].check` решает, какие пары `RU↔EN` нужно переводить.
2. **Translate** — для каждого нужного файла:
   - `parse_document_units` разбивает SOURCE на ordered units: `prose`, `table`, `fence`, `diplodoc` (`{% note %}` / `{% cut %}`); новый `prose` начинается на каждом `###`.
   - Каждый unit переводится одним FM-запросом по промпту `prompts/08_translate_segment.txt`.
   - Сборка → `apply_deterministic_cli_fixes` (idempotent, без LLM).
3. **QA** — см. ниже.
4. **Commit + push + comment** — всегда, независимо от вердикта.

## doc_verify

То же самое, но без шага 2 — RU и EN читаются с ветки PR.

## QA (одинаково для обоих режимов)

| Шаг | FM-вызовов | Промпт | Что |
|-----|------------|--------|-----|
| Compare | 1 | `05_verify_translation.txt` | Критик возвращает вердикт `ПРИНИМАТЬ` / `ПРИНИМАТЬ С ОГОВОРКАМИ` / `НЕ ПРИНИМАТЬ` |
| Fix-diff | 0–1 | `06_fix_translation.txt` | Только при `НЕ ПРИНИМАТЬ`. Критик возвращает JSON `{"fixes": [{find, replace, reason}]}`; применяется CLI-ом через точный `str.replace` |
| Re-validate | 0–1 | `07_confirm_repair.txt` | Только если fix-diff применился. Переводчик проверяет результат тем же шаблоном вердикта, что и критик |
| Heuristics | 0–1 LLM + детерминированные | `09_quality_heuristics.md` | Запускаются всегда на финальном EN; детерминированные правила в Python, остальные — один LLM-вызов |

## Модели

- **Translator** (`[models].translate`): `yandexgpt-5.1`.
- **Critic** (`[models].translation_verify`): `qwen3.6-35b-a3b`.
- **Critic fallbacks** (`YDBDOC_MODEL_VERIFY_FALLBACKS`): `qwen3-235b-a22b/latest, deepseek-v3.2/latest`. Не Yandex — критик не должен быть из той же семьи, что и переводчик.

## Гарантии

- **Никаких циклов**. Максимум 3 «тяжёлых» QA-запроса на файл (compare + fix + re-validate).
- **CI всегда зелёный**, кроме инфраструктурных ошибок (FM API лёг полностью на все фолбэки, нет прав на push, баг в коде).
- **Коммит создаётся всегда**: пользователь видит итоговый EN в translation PR + отчёт с вердиктом, блокерами, оговорками и эвристиками. Решение о мерже — за пользователем.

## Where to tweak behaviour

- Шкала серьёзности и формат отчёта критика — `prompts/05_verify_translation.txt`.
- Контракт исправителя и список запрещённых правок — `prompts/06_fix_translation.txt`.
- Шаблон повторной проверки — `prompts/07_confirm_repair.txt`.
- Правила перевода фрагментов — `prompts/08_translate_segment.txt`.
- Эвристические проверки качества — `prompts/09_quality_heuristics.md`. Добавьте новый ```yaml блок — он подхватится автоматически. Детерминированная реализация необязательна; без неё проверка делегируется LLM.
