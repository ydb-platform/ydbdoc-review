# ydbdoc-review

GitHub Action и CLI для автоматического перевода документации YDB (**RU ↔ EN**) с QA-критиком.

**Ветка v2:** `doc-translate-ng` — AST-пайплайн (parse → segment → translate → critic → render).  
Подробности для разработчиков: [ARCHITECTURE.md](ARCHITECTURE.md), [CONTRIBUTING.md](CONTRIBUTING.md), [Memory Bank](MEMORY_BANK.md).

## Что делает

1. По лейблу **`doc_translate`** на PR в `ydb-platform/ydb` находит изменённые пары `ydb/docs/ru/…` ↔ `ydb/docs/en/…`.
2. Переводит нужные файлы через Yandex AI Studio (OpenAI-compatible API).
3. Запускает **critic** + эвристики, применяет безопасные правки.
4. Пушит ветку `ydbdoc-review/pr-<N>`, открывает **translation PR**, комментирует исходный PR.
5. Лейбл **`doc_verify`** на translation PR — повторный QA без перевода, commit fix-ов, новый отчёт.

Исходная ветка PR **не меняется**. Решение о мерже translation PR — за человеком.

## Требования

- Python **3.11+** (локально) или Docker (GitHub Action).
- **Yandex AI Studio:** folder id + API key.
- **GitHub:** `GITHUB_TOKEN`; для push в форк — `GITHUB_PUSH_TOKEN` / `YDBDOC_PUSH_PAT`.

## Быстрый старт (локально)

```bash
git clone https://github.com/ydb-platform/ydbdoc-review.git
cd ydbdoc-review
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # заполните YDBDOC_YC_* и GITHUB_*
```

Клон репозитория с доками и checkout PR:

```bash
git clone https://github.com/ydb-platform/ydb.git /path/to/ydb
cd /path/to/ydb
gh pr checkout <N>
git fetch origin main
```

Dry-run (без записи и комментариев):

```bash
ydbdoc-review run \
  --repo ydb-platform/ydb \
  --pr <N> \
  --repo-path /path/to/ydb \
  --merge-base-with origin/main \
  --dry-run
```

Полный прогон:

```bash
ydbdoc-review run \
  --repo ydb-platform/ydb \
  --pr <N> \
  --repo-path /path/to/ydb \
  --merge-base-with origin/main
```

Verify на translation PR:

```bash
ydbdoc-review verify \
  --repo ydb-platform/ydb \
  --pr <translation_pr> \
  --repo-path /path/to/ydb \
  --merge-base-with origin/main
```

## CLI

| Команда | Назначение |
|---------|------------|
| `run` | `doc_translate` — перевод + ветка + PR + комментарии |
| `verify` | `doc_verify` — critic-only QA на translation PR |
| `list-models` | Цепочки моделей из config; `--live` — GET `/v1/models` |
| `translate-file` | Один `.md` локально, без GitHub |
| `extract` | Сегменты файла (debug), `--format json\|text` |

```bash
ydbdoc-review translate-file docs/ru/page.md -o /tmp/en.md
ydbdoc-review extract docs/ru/page.md --format json
ydbdoc-review list-models --live
```

Эквивалент: `python -m ydbdoc_review …`

## Конфигурация

- Defaults: `src/ydbdoc_review/config/default.yaml` (в пакете).
- Overrides: env `YDBDOC_<SECTION>_<KEY>` — см. `.env.example` и [Memory Bank §13](docs/memory-bank/06-llm-config.md).
- Секреты **только** из env: `YDBDOC_YC_FOLDER_ID`, `YDBDOC_YC_API_KEY`, `GITHUB_TOKEN`, `GITHUB_PUSH_TOKEN`.

## GitHub Action

`action.yml` в корне этого репозитория. В workflow репозитория **ydb**:

```yaml
uses: ydb-platform/ydbdoc-review@v0.1.0   # тег на main после merge v2
with:
  repo: ${{ github.repository }}
  pr: ${{ github.event.pull_request.number }}
  merge_base_with: origin/${{ github.event.pull_request.base.ref }}
  mode: run          # или verify
  dry_run: "false"
  no_commit: "false"
env:
  YDBDOC_YC_FOLDER_ID: ${{ secrets.YANDEX_CLOUD_FOLDER_DOC_REVIEW }}
  YDBDOC_YC_API_KEY: ${{ secrets.YANDEX_CLOUD_API_KEY_DOC_REVIEW }}
  GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  GITHUB_PUSH_TOKEN: ${{ secrets.YDBDOC_PUSH_PAT }}
  YDBDOC_REPO_PATH: ${{ github.workspace }}
```

Примеры workflow: [`examples/`](examples/).

**Checkout:** `fetch-depth: 0` и `git fetch` базовой ветки PR обязательны для `merge-base`.

**Форк:** нужен PAT в секрете `YDBDOC_PUSH_PAT` → env `GITHUB_PUSH_TOKEN`; у автора PR — *Allow edits by maintainers*.

## Тесты

```bash
pytest tests/unit/ tests/integration/test_real_files_round_trip.py
pytest tests/integration/test_llm_smoke.py -m llm   # локально, с ключами
```

## Документация

| Документ | Аудитория |
|----------|-----------|
| [README.md](README.md) | пользователи Action / CLI |
| [ARCHITECTURE.md](ARCHITECTURE.md) | архитектура v2 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | разработчики |
| [MEMORY_BANK.md](MEMORY_BANK.md) | полный design doc (index) |

## Лицензия

Уточните лицензию при публикации (рекомендуется согласовать с политикой YDB).
