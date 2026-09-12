# Contributing to ydbdoc-review

Спасибо за интерес к проекту. v2 разрабатывается в ветке **`doc-translate-ng`**.

## Перед началом

1. Прочитайте [ARCHITECTURE.md](ARCHITECTURE.md) и [Memory Bank — Roadmap](docs/memory-bank/05-roadmap.md).
2. Установите окружение:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

3. Прогоните тесты без LLM:

```bash
pytest tests/unit/ tests/integration/test_real_files_round_trip.py
```

## Workflow разработки

Мы двигаемся **фазами** (см. roadmap):

1. Код + unit-тесты (mock LLM где нужно).
2. Coverage ~90% на затронутых пакетах.
3. Обновление Memory Bank (`docs/memory-bank/`, индекс `MEMORY_BANK.md`).
4. Коммит с префиксом `ng:` на `doc-translate-ng`.

Пример сообщения коммита:

```
ng: add navigation path detection (Phase I glue)

Detect toc*.yaml and redirect paths in PR scope for merge helpers.
```

## Стиль кода

- Python 3.11+, type hints, pydantic v2 для схем.
- `ruff` — см. `pyproject.toml` (`ruff check src tests`).
- Минимальный diff: не рефакторить несвязанный код.
- Комментарии — только для неочевидной логики.

## Тесты

| Тип | Когда |
|-----|--------|
| Unit | Каждый PR; LLM мокается |
| Fixture round-trip | Парсер/сегментация/рендер |
| `@pytest.mark.llm` | Локально, при изменении JSON-контрактов LLM |

Не добавляйте LLM smoke в default CI.

```bash
pytest tests/unit/test_translator.py -v
pytest --cov=ydbdoc_review --cov-report=term-missing tests/unit/
```

## Memory Bank

Design doc разбит на части в `docs/memory-bank/`. При изменении поведения:

- обновите соответствующий файл (pipeline → `07-pipeline.md`, config → `06-llm-config.md`, …);
- отметьте чеклист в `05-roadmap.md`;
- при необходимости — одну строку в `MEMORY_BANK.md` (index).

## CLI и Action

- CLI: `src/ydbdoc_review/cli.py`, entry `ydbdoc-review` / `python -m ydbdoc_review`.
- Docker: `Dockerfile` + `entrypoint.sh` → те же команды.
- Новые флаги Action — через `action.yml` inputs и mapping в `entrypoint.sh`.

## Pull requests

1. Ветка от `doc-translate-ng` (до merge v2 в `main`).
2. Описание: что меняется и зачем; test plan.
3. Без секретов в diff; `.env` в gitignore.

## Release / тег

Тег **`v0.1.0`** используется workflow в `ydb-platform/ydb`. Перенос тега на merge-commit v2 —
отдельное решение maintainers (не bump на `v0.2.0` без явного согласования).

## Вопросы

Открывайте issue в репозитории или обращайтесь к maintainers YDB docs tooling.
