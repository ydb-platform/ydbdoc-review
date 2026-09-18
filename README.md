# ydbdoc-review

Один конвейер RU↔EN для документации YDB. Нормативный контракт — [REQUIREMENTS_RU.md](REQUIREMENTS_RU.md).

- `doc_translate`: план по зафиксированному SHA, source-only перевод, общий цикл проверки и исправлений, новый переводной PR. Двуязычные пары пропускаются. Механические удаления/переименования публикуются без модели.
- `doc_verify`: существующие RU/EN пары, RU — оригинал; проверка и исправление EN в том же PR.
- `doc_continue`: последняя разрешённая инструкция `/ydbdoc continue <текст>`, сохранённый контекст YDB и тот же PR. Оба SHA должны совпадать; максимум три допуска продолжения на исходный PR. Уникальное короткое имя файла разрешено, неоднозначное требует уточнения.

Каждый режим проверяет ACL до модели и изменений документов, выполняет preflight до создания модели и один раз проверяет дневной бюджет перед первым модельным вызовом. Проверяется неизменяемый кандидат; GREEN относится к его SHA. Ошибки дают RED, полученные результаты доступны в draft PR. Отчёты, контекст и все оплаченные попытки записываются через общие компоненты. Контекст хранится 14 дней, финансовая история — без TTL. При отказе ACL или ошибке конфигурации исходный PR получает комментарий с причиной; модель, изменения документов и новый PR не запускаются. Ошибка закрытия SDK после завершения сохраняет итоговый вердикт и отдельно выводится как cleanup warning в stderr.

```sh
ydbdoc-review doc_translate --repo ydb-platform/ydb --pr 123
ydbdoc-review doc_verify --repo ydb-platform/ydb --pr 456
ydbdoc-review doc_continue --repo ydb-platform/ydb --pr 456
```

CLI самостоятельно получает metadata GitHub и fetch объектов по SHA в отдельный временный bare repository. Чужой checkout не исполняется. Для label-based Actions см. [examples](examples) и [action.yml](action.yml). Необязательный JSON конфигурации берут из доверенной базовой ветки. В production закрепите ревизию Action на проверенный commit SHA.

## Конфигурация

Четыре обязательные Actions variables репозитория `ydb-platform/ydb`:

| Переменная | Назначение |
| --- | --- |
| `YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE` | Общий лимит дополнительных статей; согласовано 20 |
| `YDBDOC_MAX_SOURCE_CHARACTERS` | Raw symbols всего плана; согласовано 250000 |
| `YDBDOC_ALLOWED_ACTORS` | GitHub-логины через запятую |
| `YDBDOC_DAILY_BUDGET_RUB` | Бюджет календарного дня Москвы |

Лимиты читаются из переменных, без запасных значений. `GITHUB_ACTOR` задаёт инициатора. `GITHUB_TOKEN` нужен для API и Git; необязательный `GITHUB_PUSH_TOKEN` — отдельный токен записи. Секрет сервисного аккаунта `YDB_SA_KEY` содержит JSON. Endpoint/database по умолчанию из требований; технические переопределения — `YDBDOC_YDB_ENDPOINT`, `YDBDOC_YDB_DATABASE`.

Существующий workflow продолжает передавать секреты и переменные через `env`. Action по умолчанию запускает перевод (`mode: run`); `verify` и `continue` соответствуют `doc_verify` и `doc_continue`. Имена `doc_*` также принимаются. `config` необязателен.

Без JSON используются прежние модели Yandex Cloud: перевод и исправления — `deepseek-v32` с одной альтернативой `yandexgpt-5-pro`; критик — `yandexgpt-5.1` с альтернативой `yandexgpt-5-lite`. Секреты: `YANDEX_CLOUD_FOLDER_DOC_REVIEW` и `YANDEX_CLOUD_API_KEY_DOC_REVIEW`. Непустые `YDBDOC_MODEL_TRANSLATE` / `YDBDOC_MODEL_CHECK` переопределяют основные модели; пустые значения сохраняют встроенные настройки. Для других провайдеров и моделей можно передать доверенный `--config` / Action input `config`. Четыре продуктовые переменные остаются обязательными, их workflow передаёт из `${{ vars.* }}` через `env`; Action не обращается к контексту `vars`.

Необязательный технический JSON явно задаёт три роли, primary и не более одной alternative на роль. Пример формы (замените модель, endpoint и реальные ёмкости на согласованные значения; это не тарифная рекомендация):

```json
{
  "models": {
    "translation": {"main": {"provider": "eliza", "base_url": "https://api.eliza.yandex.net", "model": "CONFIGURED_MODEL", "token_env": "MODEL_TOKEN"}},
    "critic": {"main": {"provider": "eliza", "base_url": "https://api.eliza.yandex.net", "model": "CONFIGURED_MODEL", "token_env": "MODEL_TOKEN"}},
    "repair": {"main": {"provider": "eliza", "base_url": "https://api.eliza.yandex.net", "model": "CONFIGURED_MODEL", "token_env": "MODEL_TOKEN"}}
  },
  "context_tokens": 32768,
  "max_output_tokens": 8192,
  "timeout_s": 120,
  "glossary": []
}
```

`provider` — `eliza` или `yandex_cloud`; для Yandex Cloud дополнительно требуется `folder_id`, base URL указывает OpenAI-compatible `/v1`. `alternative` имеет ту же форму, что `main`. Емкость должна подходить всем заданным endpoint. Разбиение использует консервативную верхнюю оценку UTF-8 bytes с запасом на обрамление сообщений; это может создать больше чанков, чем точный tokenizer. Глоссарий — список пар RU/EN.

Для встроенных моделей заданы проверенные синхронные тарифы Yandex AI Studio в рублях за миллион токенов: DeepSeek V3.2 — 500 вход / 800 выход / 130 кешированный вход; YandexGPT 5.1 — 800/800, Pro 5 — 1200/1200, Lite 5 — 200/200. Источники: [официальные тарифы](https://aistudio.yandex.ru/ru/docs/ai-studio/pricing), [официальный справочник DeepSeek](https://github.com/yandex-ai-studio/yandex-ai-studio-cookbook/blob/main/multi_agent/parsed_docs/pricing.md); проверено 18.09.2026. Тарифы технические и требуют обновления при изменении цен провайдера.

В явном JSON цены задаются массивом `tariffs_rub_per_million`, где запись содержит `provider`, `model`, `input`, `output` и необязательный `cached_input` (RUB за миллион токенов, Decimal-строки). Укажите только проверенные тарифы соответствующего провайдера/модели. Без тарифа или usage расходы честно неизвестны; следующий budget admission не сможет подтвердить суточную сумму. Нет USD aliases, пересчёта валют или выдуманных цен.

TLS: стандартный публичный CA bundle; для Eliza доступны `YDBDOC_ELIZA_CA_BUNDLE` (PEM-путь; в Action разместите его внутри read-only workspace). Проверка сертификатов не отключается. Модельные token_env передаются Action в контейнер по имени, значения не входят в CLI arguments.

## Техническая установка и проверки

Python 3.11+, Git, Node 24 и `@diplodoc/cli@5.61.0`:

```sh
uv sync --locked --extra dev
npm install --global @diplodoc/cli@5.61.0
uv run ruff check src tests scripts
uv build
```

`requirements.txt` — экспорт runtime-зависимостей из `uv.lock`. Dockerfile содержит тот же фиксированный YFM и Node 24. Для YDB до первого запуска подготовьте обе таблицы по [scripts/PROVISIONING.md](scripts/PROVISIONING.md). Старая схема несовместима: предусмотрено сохранение финансовой истории и проверка дневных сумм перед переключением; продукт не выполняет DDL.

PR CI выполняет весь offline-набор семью группами, каждая с `timeout 900s`, `pytest -vv`, отдельным timeout тестов и запретом сети. Повторить локально:

```sh
for group in unit-parser quality-links translate verify continue store-reports cli; do
  timeout 900s uv run python -m pytest -vv --ci-group "$group" || exit
done
```

Каждый collected nodeid назначается ровно одной группе. Component tests реально используют временные Git repositories и YFM. CLI subprocess tests подменяют HTTP/SDK и builder; runner/report/store остаются настоящими. Это локальные свидетельства, а не подтверждение deployed схемы или live API.

Открытые согласования: поведение verify для уже слитого PR; детали расхода слота продолжения при сбое после допуска; тарифы для дополнительных моделей вне встроенной конфигурации. Текущий merged-verify отказ — техническое ограничение реализации, не согласованный продуктовый запрет. Допущенное продолжение расходует слот перед factory, даже если после неё возникает ошибка.
