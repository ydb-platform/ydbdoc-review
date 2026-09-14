# Contributing to ydbdoc-review

Спасибо за интерес к проекту. Актуальная разработка ведётся от `main`.

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
4. Один scoped commit с понятным conventional-заголовком, например `fix:` или `docs:`.

Пример сообщения коммита:

```
docs: describe navigation path detection

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


### Согласование требований с владельцем продукта

После каждого явного решения владельца продукта сразу обновляйте
`REQUIREMENTS_RU.md`, соответствующий раздел `docs/memory-bank/` и индекс
`MEMORY_BANK.md`, до перехода к следующему вопросу. Не откладывайте запись
до реализации кода. Перед правкой перечитывайте актуальные файлы из `main`:
параллельно может идти разработка. Сохраняйте чужие изменения и публикуйте
согласованные решения отдельным scoped commit. Рекомендации агента и открытые
вопросы не выдавайте за утверждённые требования; статус реализации указывайте отдельно.

## CLI и Action

- CLI: `src/ydbdoc_review/cli.py`, entry `ydbdoc-review` / `python -m ydbdoc_review`.
- Docker: `Dockerfile` + `entrypoint.sh` → те же команды.
- Новые флаги Action — через `action.yml` inputs и mapping в `entrypoint.sh`.

## Pull requests

1. Ветка от актуального `main` или от явно зафиксированной release-base.
2. Описание: что меняется и зачем; test plan.
3. Без секретов в diff; `.env` в gitignore.

## Release / тег

Подвижный тег **`v0.1.0`** служит продакшен-ссылкой для workflow в
`ydb-platform/ydb`. Его перемещают только после полного набора тестов и
независимого review. Публикация считается проверенной, когда тег и
`main` указывают на один commit; результативные `doc_verify` и docs build должны
относиться к одному SHA translation PR.

## Вопросы

Открывайте issue в репозитории или обращайтесь к maintainers YDB docs tooling.
