# ydbdoc-review

Утилита для CI и командной строки: по pull request в документации YDB находит пары статей **русский ↔ английский** (`.md` под `ydb/docs/`), **дешёвой** моделью проверяет, есть ли перевод и **согласованы ли смыслы**, при отсутствии перевода вызывает **более сильную** модель, **пишет** файл перевода, делает **commit** и **push** в ветку head PR и оставляет **комментарий** в PR.

> **Безопасность:** не коммитьте API-ключи. Для локалки — файл `.env` (в `.gitignore`), в CI — **Secrets** GitHub.

## Требования

- Python **3.11+** (или только Docker для GitHub Action).
- **Токен GitHub** с правами на чтение PR и (при необходимости) **push** в репозиторий, откуда открыт PR (**head**).
- Каталог в **Yandex Cloud** и **API-ключ** для OpenAI-совместимого endpoint Foundation Models ([документация](https://yandex.cloud/ru/docs/foundation-models/)).

---

## Локальное тестирование

### 1. Клонировать этот репозиторий и создать venv

Рекомендуемый путь к проекту: `~/ydbdoc-review` (или любой другой).

```bash
cd /Users/iuriisintiaev/ydbdoc-review   # подставьте свой путь
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH="$(pwd)/src"
```

`PYTHONPATH` нужен, если не ставили пакет через `pip install -e .`.

### 2. Файл `.env`

```bash
cp .env.example .env
```

Заполните как минимум:

| Переменная | Назначение |
|------------|------------|
| `YANDEX_CLOUD_FOLDER_DOC_REVIEW` или `YANDEX_CLOUD_FOLDER` или `YC_FOLDER_ID` | ID каталога (`b1…`). Для CI в `ydb` удобнее имена с суффиксом `_DOC_REVIEW`. |
| `YANDEX_CLOUD_API_KEY_DOC_REVIEW` или `YANDEX_CLOUD_API_KEY` или `YC_API_KEY` | Ключ API Foundation Models. |
| `YANDEX_CLOUD_BASE_URL` | Обычно `https://ai.api.cloud.yandex.net/v1` (если не задано — используется это значение по умолчанию). |
| `GITHUB_TOKEN` | PAT или вывод `gh auth token` после `gh auth login`. |
| `GITHUB_PUSH_TOKEN` | Необязательно; по умолчанию = `GITHUB_TOKEN`. Для PR из **чужого форка** часто нужен отдельный PAT с push в этот форк. |

Имена **моделей** (проверка / перевод) задаются в **`ydbdoc-review.toml`**: секция `[models]`, ключи `check` и `translate`. По умолчанию используется файл из пакета (`src/ydbdoc_review/ydbdoc-review.toml`); переопределение — свой `ydbdoc-review.toml` в каталоге запуска или **`YDBDOC_CONFIG`**. Значения из TOML можно сменить переменными **`YDBDOC_MODEL_CHECK`** и **`YDBDOC_MODEL_TRANSLATE`** (удобно в CI).

Список id моделей в вашем каталоге (если шлюз отдаёт `GET /v1/models`):

```bash
export PYTHONPATH="$(pwd)/src"
python -m ydbdoc_review list-models
```

Официальный перечень в интерфейсе: [Yandex AI Studio — Model gallery](https://aistudio.yandex.ru/model-gallery). Название вендора: **DeepSeek**; строка для API может выглядеть как `deepseek-v3.2/latest` — всё равно сверяйте с тем, что показано у вас в консоли.

**Выключатель всего `run`** (удобно в CI репозитория-документации, аналогично флагам Diplodoc): переменная **`YDBDOC_REVIEW_ENABLED`** (`false` / `0` / `off` — команда сразу завершается успешно, без GitHub и без FM). Либо в `ydbdoc-review.toml` секция **`[feature]`** и ключ **`review_enabled = false`**. Если заданы и env, и TOML, **приоритет у env**.

Переменные из `.env` подхватываются автоматически (`python-dotenv`).

Вызовы идут в **OpenAI-совместимый** endpoint Foundation Models (`client.responses` и URI вида `gpt://<folder>/<model>`). Переменные **`ANTHROPIC_BASE_URL`** / **`ANTHROPIC_AUTH_TOKEN`** (шлюз Eliza и т.п.) этим кодом **не** используются. **`YC_INDEXER_API_KEY`** к обзору доков не относится.

`export PATH=…` и **`NODE_EXTRA_CA_CERTS`** задавайте в shell-профиле (в `.env` для Python они не нужны; для HTTPS из Python при корпоративном CA обычно используют `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`, если потребуется).

### 3. Клон репозитория `ydb` и ветка PR

```bash
cd ~/src   # или другой каталог
git clone https://github.com/ydb-platform/ydb.git
cd ydb
git fetch origin pull/<НОМЕР_PR>/head:pr-<НОМЕР_PR>
git checkout pr-<НОМЕР_PR>
# альтернатива:
# gh pr checkout <НОМЕР_PR>
git fetch origin main
```

Для расчёта изменённых файлов через `git merge-base` нужна актуальная база (часто **`origin/main`**).

### 4. Первый прогон: только проверка, без записи и без комментария в PR

```bash
cd /path/to/ydbdoc-review
source .venv/bin/activate
export PYTHONPATH="$(pwd)/src"

python -m ydbdoc_review run \
  --repo ydb-platform/ydb \
  --pr <НОМЕР_PR> \
  --repo-path /path/to/ydb \
  --merge-base-with origin/main \
  --dry-run
```

Так проверяются список затронутых пар `ru`/`en`, вызов **проверочной** модели и в консоли выводится **превью** комментария к PR. Файлы не меняются, push и комментарий не выполняются.

### 5. Полный прогон: перевод (при необходимости), коммит, push, комментарий

Уберите `--dry-run`. Нужны права **push в ветку head** этого PR (часто достаточно того же `GITHUB_TOKEN` для ветки в основном репо; для **приватного форка** — отдельный **`GITHUB_PUSH_TOKEN`**).

```bash
python -m ydbdoc_review run \
  --repo ydb-platform/ydb \
  --pr <НОМЕР_PR> \
  --repo-path /path/to/ydb \
  --merge-base-with origin/main
```

Дополнительные флаги:

| Флаг | Назначение |
|------|------------|
| `--dry-run` | Ничего не писать на диск, не коммитить, не пушить, не комментировать в PR. |
| `--no-commit` | Записать сгенерированные переводы в дерево `--repo-path` (если модель их запросила), **без** `git commit`, **без** push и **без** комментария в PR — только превью в консоли. |
| `--no-push` | Сделать коммит только в локальном `--repo-path`, без `git push`. |
| `--no-comment` | Не создавать комментарий в PR. |

### 6. Промпты и лимиты анализа

- Тексты промптов лежат в каталоге [`prompts/`](prompts/) — их можно править без изменения кода.
- Каталог с промптами можно переопределить: **`YDBDOC_PROMPTS_DIR`**.
- Длинные статьи для **проверочной** модели укорачиваются; бюджет задаётся **`YDBDOC_MAX_ANALYZE_CHARS`** (по умолчанию см. `src/ydbdoc_review/config.py`).

### 7. Запуск без локального клона `ydb`

Если **не** указать `--repo-path`, инструмент возьмёт список файлов из GitHub API и при записи **клонирует head-репозиторий** PR. Для отладки удобнее всегда иметь локальный checkout и **`--repo-path`**.

Переменная **`YDBDOC_REPO_PATH`** (равная пути к корню репо с доками) используется в CI вместо `--repo-path`.

---

## Где взять ключи

### Yandex Cloud / AI Studio

1. [Консоль Yandex Cloud](https://console.yandex.cloud/) → каталог → сервисные аккаунты / API-ключи (по политике вашей организации).
2. Либо **Yandex AI Studio** — тот же каталог и OpenAI-совместимый endpoint, см. [совместимость с OpenAI API](https://yandex.cloud/ru/docs/foundation-models/concepts/openai-compatibility).

### GitHub

- [Создание PAT](https://github.com/settings/tokens): для push и комментариев к PR обычно нужен scope **`repo`** (для приватных репозиториев и форков — обязательно уточнить минимальные права).
- Локально: `export GITHUB_TOKEN="$(gh auth token)"` после `gh auth login`.

### Push в форк автора PR

Токен **`GITHUB_TOKEN`** в workflow на стороне базового репозитория **не может** пушить в чужой форк. Нужен **PAT** пользователя, у которого есть push в ветку в этом форке, в переменной **`GITHUB_PUSH_TOKEN`**.

---

## CLI (кратко)

```bash
export PYTHONPATH="$(pwd)/src"
python -m ydbdoc_review list-models
python -m ydbdoc_review run --repo <owner>/<repo> --pr <N> \
  [--repo-path <путь>] [--merge-base-with origin/main] \
  [--dry-run] [--no-commit] [--no-push] [--no-comment]
```

Аргумент **`--repo`** — репозиторий, **в котором открыт PR** (например `ydb-platform/ydb`).

---

## Репозиторий `ydbdoc-review`, `action.yml` и релизы

### Зачем `action.yml` и где он лежит

Файл **`action.yml` лежит только в корне репозитория `ydbdoc-review`** (рядом с `Dockerfile` и `entrypoint.sh`). Это **не** файл для копирования в `ydb`. GitHub при записи `uses: ydb-platform/ydbdoc-review@v0.1.0` в workflow репозитория **ydb** скачивает указанный **тег** из репозитория **`ydb-platform/ydbdoc-review`**, читает оттуда `action.yml`, подставляет входные параметры `with:` в переменные `INPUT_*` и **собирает образ** из этого же коммита. То есть `action.yml` — это «манифест» публикуемого GitHub Action.

### Что значит «опубликованный тег» и куда нажимать

**Тег** — это обычная git-метка на коммите в **репозитории `ydb-platform/ydbdoc-review`** (или `your-login/ydbdoc-review`, если форк), например **`v0.1.0`**. В workflow в **ydb** пишется:

`uses: ydb-platform/ydbdoc-review@v0.1.0`

GitHub подставляет ref **`v0.1.0`** (или полный SHA) и кеширует образ.

**Как опубликовать:** в GitHub откройте **`ydb-platform/ydbdoc-review`** → **Releases** → **Create a new release** → поле **Choose a tag**: создайте тег `v0.1.0` (или через `git tag v0.1.0 && git push origin v0.1.0`) → опубликуйте release. До первого тега `uses: …@v0.1.0` не заработает. Для черновых проверок можно временно указать `@main`, но для продакшена лучше фиксировать версию тегом.

Если action лежит под другим owner/org, замените префикс в `uses:` соответственно.

### Где хранить секреты Yandex и GitHub

Workflow с вызовом action **выполняется в том репозитории, где лежит YAML** — у вас это **`ydb`**. Секреты **`YANDEX_CLOUD_*_DOC_REVIEW`** (и при необходимости **`GITHUB_PUSH_TOKEN`**) заводятся в **настройках репозитория `ydb`**:

**`ydb` на GitHub** → **Settings** → **Secrets and variables** → **Actions** → **New repository secret** — для ydbdoc-review заведите **`YANDEX_CLOUD_FOLDER_DOC_REVIEW`** и **`YANDEX_CLOUD_API_KEY_DOC_REVIEW`** (значения без кавычек). Старые имена `YANDEX_CLOUD_*` без суффикса по-прежнему поддерживаются в коде и в `.env`.

Репозиторий **`ydbdoc-review`** при этом хранит **только код** action и релизы; секреты Yandex там **не обязательны**, если вы не запускаете отдельный workflow внутри `ydbdoc-review`, который сам ходит в облако. Исключение: organization-level secrets, если админ выдал доступ и `ydb` их наследует — тогда создаёте секрет на уровне организации и подключаете к репо `ydb`.

---

## GitHub Actions

### Запуск по лейблу `doc_translate` (как отдельный workflow)

Чтобы **не** гонять перевод на каждый push, а только по команде с PR (аналогично тому, как вы дергаете Diplodoc по своим правилам), заведите **второй** workflow, который слушает только событие **`labeled`** и условие на имя лейбла.

Готовый фрагмент: [`examples/ydb-github-doc-translate-on-label.yml`](examples/ydb-github-doc-translate-on-label.yml) — скопируйте в репозиторий с доками как `.github/workflows/ydbdoc-review.yml`. В примере указано `uses: ydb-platform/ydbdoc-review@v0.1.0`. Для локального сабмодуля можно `uses: ./path/to/ydbdoc-review`.

Поля **`with:`** у action (как у `docs-build-action`):

| Input | Назначение |
|-------|------------|
| `repo` | `owner/name` репозитория, где открыт PR. |
| `pr` | Номер PR. |
| `merge_base_with` | Второй ref для `git merge-base` (по умолчанию `origin/main`; в примере — `origin/${{ base.ref }}`). |
| `dry_run` | `"true"` → флаг `--dry-run`. |
| `no_commit` | `"true"` → флаг `--no-commit`. |

После `actions/checkout` обязательно **`fetch-depth: 0`** и `git fetch` базовой ветки PR, иначе `merge-base` не найдётся.

### Пример job внутри существующего workflow (по push)

После `actions/checkout` с `ref: ${{ github.event.pull_request.head.sha }}` (как в [`docs_build.yaml`](https://github.com/ydb-platform/ydb/blob/main/.github/workflows/docs_build.yaml)):

```yaml
  translate-review:
    if: ${{ github.event.pull_request.head.repo.fork == false && vars.YDBDOC_REVIEW_ENABLED != 'false' }}
    needs: add-label
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0

      - name: Fetch base for merge-base
        run: git fetch origin ${{ github.event.pull_request.base.ref }}:refs/remotes/origin/${{ github.event.pull_request.base.ref }}

      - name: Documentation translation review
        uses: ydb-platform/ydbdoc-review@v0.1.0
        with:
          repo: ${{ github.repository }}
          pr: ${{ github.event.pull_request.number }}
          merge_base_with: origin/${{ github.event.pull_request.base.ref }}
        env:
          YANDEX_CLOUD_FOLDER_DOC_REVIEW: ${{ secrets.YANDEX_CLOUD_FOLDER_DOC_REVIEW }}
          YANDEX_CLOUD_API_KEY_DOC_REVIEW: ${{ secrets.YANDEX_CLOUD_API_KEY_DOC_REVIEW }}
          YDBDOC_REVIEW_ENABLED: ${{ vars.YDBDOC_REVIEW_ENABLED }}
          YDBDOC_MODEL_CHECK: ${{ vars.YDBDOC_MODEL_CHECK }}
          YDBDOC_MODEL_TRANSLATE: ${{ vars.YDBDOC_MODEL_TRANSLATE }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_PUSH_TOKEN: ${{ secrets.GITHUB_TOKEN }}  # при форке — PAT в секрет и сюда
          YDBDOC_REPO_PATH: ${{ github.workspace }}
```

**PR из форка:** либо отключите job (`if:`, как выше), либо задайте **`GITHUB_PUSH_TOKEN`** с правами push в форк и скорректируйте `permissions` / условия.

### Секреты и variables в репозитории

| Secret | Пример |
|--------|--------|
| `YANDEX_CLOUD_FOLDER_DOC_REVIEW` | `b1…` (ID каталога) |
| `YANDEX_CLOUD_API_KEY_DOC_REVIEW` | строка API-ключа FM |

В `env` workflow передаются **имена переменных**, которые читает приложение; имена **секретов в GitHub** могут совпадать с ними, как в примере выше. Допустимы и старые имена секретов с маппингом, например `YANDEX_CLOUD_FOLDER: ${{ secrets.YANDEX_CLOUD_FOLDER }}` — код примет и их (см. таблицу локальных переменных в начале README).

| Variable | Пример |
|----------|--------|
| `YDBDOC_REVIEW_ENABLED` | `true` / `false` — глобально включить или выключить шаг (пустое = как в `ydbdoc-review.toml`). |
| `YDBDOC_MODEL_CHECK` | Необязательно, если заданы модели в `ydbdoc-review.toml` в образе/репо или устраивают встроенные значения. |
| `YDBDOC_MODEL_TRANSLATE` | То же; иначе переопределение slug для перевода. |

Встроить job можно рядом с существующим `build-docs` в `.github/workflows/docs_build.yaml`, указав `uses:` на ваш тег релиза этого action или на vendored-путь (`./tools/ydbdoc-review`).

---

## Как это работает

1. **Список изменённых файлов:** GitHub API «files changed» в PR **или** локально `git merge-base` + `git diff --name-only`.
2. **Пары:** пути `ydb/docs/ru/…` ↔ `ydb/docs/en/…` с тем же хвостом (корень задаётся **`DOCS_SRC_ROOT`**, по умолчанию `ydb/docs`).
3. **Проверка:** один запрос к «дешёвой» модели с усечёнными текстами и ответом в JSON.
4. **Перевод:** «дорогая» модель вызывается только если для пары указано `needs_generation_for`: `en` или `ru`.
5. **Git:** коммит в дереве checkout; **push** в ветку **`head.ref`** удалённого **head** репозитория PR.

## Ограничения

- Для проверки длинные страницы **усечены** (`YDBDOC_MAX_ANALYZE_CHARS`).
- Если оба языка есть, но расходятся по смыслу, инструмент **не перезаписывает** файлы автоматически — в комментарии будет блок про ручной разбор.
- Не поддерживаются бинарники и не-UTF-8 как «текст статьи».

## Лицензия

При публикации на GitHub задайте лицензию явно (например Apache-2.0 в духе YDB или другую по выбору владельца репозитория).
