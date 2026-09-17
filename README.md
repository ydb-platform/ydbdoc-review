# ydbdoc-review

GitHub Action и CLI для перевода документации YDB между русским и английским языками, проверки и исправления перевода.

## Документы проекта

- [REQUIREMENTS_RU.md](REQUIREMENTS_RU.md) — единственный действующий контракт, включая режимы, проверки, публикацию, доступ, лимиты и стоимость.
- [.cursor/knowledge-bank.md](.cursor/knowledge-bank.md) — краткая копия требований для инструментов.
- [CONTRIBUTING.md](CONTRIBUTING.md) — установка и проверка изменений.

Требования согласованы; соответствие существующего кода им ещё предстоит обеспечить. Старые архитектурные описания, планы и отчёты удалены из текущего дерева; при необходимости они доступны в Git-истории.

## Разработка

Нужен Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m ydbdoc_review --help
pytest
ruff check src tests
```

Настройки текущей реализации: [.env.example](.env.example). Интерфейс Action: [action.yml](action.yml); примеры workflow — [examples/](examples/). Эти файлы относятся к существующей реализации и требуют сверки при переходе к новому контракту.

Markdown-файлы в `src/ydbdoc_review/prompts/` — рабочие промпты, в `tests/` — тестовые данные. Они не являются дополнительными требованиями и обновляются вместе с кодом и тестами.

План автоматической приёмки: [каталог сценариев](tests/contract/README.md).
Его `pending`-сценарии ещё не подтверждают реализацию требований. Независимые
тестовые fixtures проверяются командой `pytest -v tests/contract`.
