# Архитектура перехода на структурный перевод

## Основание

Контракт: `a128c38160e5322296f2358aae4b63ae0b9b51a4`, независимая сверка: `ceeb7bb7e78421ccdd899e988c196354a63fb185`. Источник кода проверялся по remote `main`; граф кода устарел и не использовался как источник ревизии.

Выбран простой подход: `DocumentPlan` описывает последовательные узлы файла. Модель получает цельный Markdown/YFM чанк. При сборке берутся только переводимые текстовые поля, а технические поля всегда восстанавливаются из исходных байтов. Никаких маркеров, JSON-массивов и новых глобальных обходов репозитория.

## Очередь задач

1. **Вернуть collection и ограничить тесты 60 секундами.** `tests/contract/test_report_f10.py`, `pyproject.toml`, тесты с `pytest.mark.timeout(120)`. Убрать импорт удалённого `initial_description`, заменить отменённые ожидания description и артефактов проверкой единственного comment по §6.1. Убрать overrides выше 60. Проверка: `pytest --collect-only -q --timeout=60 --timeout-method=signal` и изменённые тесты.
2. **Добавить план документа и сборку.** Создать `src/ydbdoc_review/structure.py`, `tests/unit/test_structure.py`, `tests/fixtures/structure/`; использовать `parsing/markdown_parser.py` и `parsing/inline_locations.py`. Интерфейсы: `plan_document(source: str, *, path: str) -> DocumentPlan`, `assemble_document(plan: DocumentPlan, candidate: str) -> AssemblyResult`. Проверить identity-сборку, сохранность технических частей, добавление/удаление узлов и небезопасное экранирование.
3. **Ограничить перевод комментариев.** `src/ydbdoc_review/validation/code_comments.py`, `structure.py`, `test_structure.py`. Только `#` для Python/Bash/YAML и `//`, `/* ... */` для C++/Java/JavaScript. При сомнении сохранить code-блок. Зависит от 2.
4. **Перевести первичный перевод и chunking на цельный Markdown/YFM.** `document.py`, prompt, unit и contract tests. Сохранить публичный `translate_document`, заменить `protect`, `restore`, `ProtectedDocument` и marker chunking на план. Делить только между блоками. Пустой, усечённый, внешне обёрнутый или несопоставимый ответ не запускает fallback. Зависит от 2–3.
5. **Подключить план к критику и repair.** `quality.py`, `quality_loop.py`, связанные unit/contract tests. Критик видит собранный файл; repair работает целыми проблемными чанками, сохраняет предыдущий кандидат при неудаче. Зависит от 4.
6. **Ограничить ссылки областью плана.** `quality_loop.py`, `runner.py`, `links.py`, link tests. Всегда передавать выбранные пути в `check_links`, не проверять дерево целиком. Зависит от 5.
7. **Сериализовать план для verify/continue.** `document.py`, `store.py`, `verify.py`, `continuation.py` и tests. Сохранить план, ID, диапазоны и статусы без индекса репозитория. Старый marker-контекст не интерпретировать молча. Зависит от 4–6.
8. **Финальная приёмка трёх режимов.** 15–20 быстрых локальных фикстур, contract tests для draft RED, пустого PR, repair, SHA, одного comment и стоимости. После предыдущих задач: `pytest -q --timeout=60 --timeout-method=signal --durations=20`.

## Тестовая база

Cleanup `4408e5152709ce23496cf2d1170f147a93599b6a` удалил только два marker-only набора, 10 сценариев. Соседние тесты: 132 passed, 10 deselected, 2.24 s, максимум 0.21 s. Полный suite RED на collection: `test_report_f10.py` импортирует удалённый `initial_description`; диагностический прогон показал около 25 overrides 120 секунд и был остановлен на 17%. Наблюдённых тестов дольше минуты нет.

Каждая задача выполняется новым разработчиком и проверяется новым независимым тестером. Только один разработчик работает с `main` одновременно. Каждый шаг отдельным commit.
