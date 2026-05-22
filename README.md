# ydbdoc-review

Утилита для CI и командной строки: по pull request в документации YDB находит пары статей **русский ↔ английский** (`.md` под `ydb/docs/`), **дешёвой** моделью проверяет, есть ли перевод и **согласованы ли смыслы**, при отсутствии перевода вызывает **более сильную** модель и **не трогает ветку исходного PR**: перевод коммитится в отдельную ветку `ydbdoc-review/pr-<N>`, открывается **отдельный PR** в head-репозитории, в исходный PR уходит **комментарий** со ссылкой и порядком действий (сначала смержить перевод, обновить ветку, при необходимости снова `doc_translate`).

Отдельный лейбл **`doc_verify`** (`mode: verify`): на **translation PR** или **bilingual PR** (RU+EN в одной ветке) тот же цикл **критик → repair → переводчик**, что после автоперевода; RU и EN берутся **с ветки этого PR** (не с `main`).

> **Безопасность:** не коммитьте API-ключи. Для локалки — файл `.env` (в `.gitignore`), в CI — **Secrets** GitHub.

## Требования

- Python **3.11+** (или только Docker для GitHub Action).
- **Токен GitHub** с правами на чтение PR и (при необходимости) **push** в репозиторий, откуда открыт PR (**head**).
- **OpenAI-совместимый LLM**: по умолчанию [Yandex Foundation Models](https://yandex.cloud/ru/docs/foundation-models/) (каталог + ключ); можно подставить **другой** хост (`OPENAI_BASE_URL` / `OPENAI_API_KEY` и т.д., см. ниже).

---

## Локальное тестирование

### 1. Клонировать этот репозиторий и создать venv

Рекомендуемый путь к проекту: `~/ydbdoc-review` (или любой другой).

```bash
cd ~/ydbdoc-review   # подставьте свой путь
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
| `YANDEX_CLOUD_FOLDER_DOC_REVIEW` или `YANDEX_CLOUD_FOLDER` или `YC_FOLDER_ID` | ID каталога Yandex Cloud (`b1…`). **Нужен только** если `YANDEX_CLOUD_BASE_URL` (или дефолт) указывает на **Yandex** (`*.yandex.net`). Для OpenAI / vLLM / другого хоста можно не задавать. |
| `YANDEX_CLOUD_API_KEY_DOC_REVIEW` или `YANDEX_CLOUD_API_KEY` или `YC_API_KEY` или **`OPENAI_API_KEY`** или **`YDBDOC_LLM_API_KEY`** | Ключ к выбранному API. |
| `YANDEX_CLOUD_BASE_URL` или **`OPENAI_BASE_URL`** или **`YDBDOC_LLM_BASE_URL`** | База OpenAI-совместимого API, обычно с суффиксом `/v1`. По умолчанию — Yandex FM `https://ai.api.cloud.yandex.net/v1`. Пример OpenAI: `https://api.openai.com/v1`. |
| `GITHUB_TOKEN` | PAT или вывод `gh auth token` после `gh auth login`. |
| `GITHUB_PUSH_TOKEN` | Только **локально** в `.env`: необязательный второй PAT для `git push` в ветку форка; по умолчанию = `GITHUB_TOKEN`. В **GitHub Actions** такой PAT не называют секретом `GITHUB_*` (запрещено платформой): заводите **`YDBDOC_PUSH_PAT`** и в workflow передаёте `GITHUB_PUSH_TOKEN: ${{ secrets.YDBDOC_PUSH_PAT }}` (см. «Push в форк»). |

Имена **моделей** (проверка / перевод) задаются в **`ydbdoc-review.toml`**: секция `[models]`, ключи `check` и `translate`. По умолчанию используется файл из пакета (`src/ydbdoc_review/ydbdoc-review.toml`); переопределение — свой `ydbdoc-review.toml` в каталоге запуска или **`YDBDOC_CONFIG`**. Значения из TOML можно сменить переменными **`YDBDOC_MODEL_CHECK`** и **`YDBDOC_MODEL_TRANSLATE`** (удобно в CI).

**Проверка и исправление перевода** (в TOML: **`translation_self_check`**, **`translation_repair`**; модель-критик — **`[models].translation_verify`**): после `doc_translate` и при **`doc_verify`** критик сравнивает RU↔EN; при существенных замечаниях переписывает EN, модель-переводчик подтверждает исправление. Исправления коммитятся в ветку PR (translation или bilingual); отчёт — комментарий в PR. Отключить только repair: **`translation_repair = false`** или **`YDBDOC_TRANSLATION_REPAIR=false`**. Для verify без коммита: **`no_commit: "true"`** в action или `--no-commit` в CLI.

**Строгий перевод (длинные статьи):** при размере исходника ≥ `YDBDOC_TRANSLATE_BY_SECTION_MIN_CHARS` (8000) перевод и QA идут **по разделам `##`**: неизменённые разделы копируются из EN, меняются только затронутые PR diff. Полный перевод файла — только если EN нет или «протух»; тогда тоже по разделам, если включён `YDBDOC_TRANSLATE_BY_SECTION` (по умолчанию да). **Quality gate** (`YDBDOC_TRANSLATION_QUALITY_GATE=1`): при `too_short`, `missing_tabs`, обрыве fences или кириллице в EN workflow падает **до коммита**. Repair критиком для файлов >30k символов — **только по разделам**.

**Другой провайдер (не Yandex):** задайте **`OPENAI_BASE_URL`** (или `YDBDOC_LLM_BASE_URL`) на корень совместимого API и **`OPENAI_API_KEY`** (или `YDBDOC_LLM_API_KEY`). Каталог Yandex не нужен. В `ydbdoc-review.toml` или в env укажите **ид модели в формате этого провайдера** (например `gpt-4o-mini`, `gpt-4o`). Для Yandex FM по-прежнему можно задать полный URI в `gpt://…` в TOML — тогда префикс каталога не добавляется автоматически.

Список id моделей в вашем каталоге (если шлюз отдаёт `GET /v1/models`):

```bash
export PYTHONPATH="$(pwd)/src"
python -m ydbdoc_review list-models
```

Официальный перечень в интерфейсе: [Yandex AI Studio — Model gallery](https://aistudio.yandex.ru/model-gallery). Название вендора: **DeepSeek**; для cross-check перевода по умолчанию используется **`deepseek-v4-flash`** (URI `gpt://<идентификатор_каталога>/deepseek-v4-flash`; модель V3.2 снята с поддержки с 28.05.2026). Сверяйте slug с консолью.

**Выключатель всего `run`** (удобно в CI репозитория-документации, аналогично флагам Diplodoc): переменная **`YDBDOC_REVIEW_ENABLED`** (`false` / `0` / `off` — команда сразу завершается успешно, без GitHub и без FM). Либо в `ydbdoc-review.toml` секция **`[feature]`** и ключ **`review_enabled = false`**. Если заданы и env, и TOML, **приоритет у env**.

Переменные из `.env` подхватываются автоматически (`python-dotenv`).

Вызовы идут в **OpenAI-совместимый** endpoint Foundation Models (`client.responses` и URI вида `gpt://<folder>/<model>`). 

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

### 5. Полный прогон: перевод, отдельная ветка, PR с переводом, комментарий в исходном PR

Уберите `--dry-run`. Нужны права **push в head-репозиторий** PR (новая ветка `ydbdoc-review/pr-<N>`, не ветка исходного PR) и **создание pull request** в том же репозитории. Для ветки в основном репо часто хватает **`GITHUB_TOKEN`** с `contents: write` и `pull-requests: write`; для **форка** — PAT: в `.env` это **`GITHUB_PUSH_TOKEN`**, в Actions — секрет **`YDBDOC_PUSH_PAT`** (см. «Push в форк»).

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
| `--no-push` | Создать коммит на локальной ветке `ydbdoc-review/pr-<N>`, без push и без открытия PR с переводом. |
| `--no-comment` | Не создавать комментарий в PR. |

### 6. Промпты и лимиты анализа

- Тексты промптов лежат в каталоге [`prompts/`](prompts/) — их можно править без изменения кода.
- Каталог с промптами можно переопределить: **`YDBDOC_PROMPTS_DIR`**.
- Длинные статьи в **шаге анализа** по умолчанию в JSON уходят **целиком** (без усечения `ru_text`/`en_text`). Чтобы уложиться в лимит входа FM (~32 000 токенов), check-модель вызывается **несколькими батчами**: в каждом запросе только часть пар, размер тела `{"pairs":[...]}` не больше **`YDBDOC_ANALYZE_MAX_JSON_CHARS`** (по умолчанию **24000** символов на батч). Если **одна** пара сама по себе не влезает, для неё поля укорачиваются с предупреждением в логе. Опционально: **`YDBDOC_ANALYZE_TRUNCATE_CHARS`** (положительное число) — усечь тела в каждом батче. Фрагменты **`ru_diff_vs_base` / `en_diff_vs_base`** в JSON по умолчанию до **500000** символов на поле (**`YDBDOC_ANALYZE_DIFF_MAX`**). Полные unified diff для эвристик override хранятся локально в `pair_diffs`, независимо от усечения в JSON для модели.

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
- Для push из **Actions** в форк: PAT кладите в секрет **`YDBDOC_PUSH_PAT`** (не в секрет с префиксом `GITHUB_`).

### Push в форк автора PR

Токен **`GITHUB_TOKEN`** в workflow на стороне базового репозитория **не может** пушить в чужой форк. Нужен **PAT** (сначала можно ваш, позже — у machine user / бота) с правом пуша в ветку head PR при включённой у автора опции **«Allow edits and access to secrets by maintainers»**.

Имя **секрета в GitHub** не может начинаться с префикса **`GITHUB_`** (ограничение платформы). PAT для push храните как **`YDBDOC_PUSH_PAT`**. В `env` у шага с action укажите **имя переменной**, которое читает код, — **`GITHUB_PUSH_TOKEN`**, и подставьте значение из секрета:

`GITHUB_PUSH_TOKEN: ${{ secrets.YDBDOC_PUSH_PAT }}`

Если секрет **не** создан или пустой, в коде используется тот же токен, что и **`GITHUB_TOKEN`** (удобно для PR из ветки в основном репо).

#### Как завести секрет `YDBDOC_PUSH_PAT` (временно ваш PAT)

1. Откройте репозиторий с доками и workflow, например **`https://github.com/ydb-platform/ydb`** → **Settings** → **Secrets and variables** → **Actions**.
2. **New repository secret** → имя **`YDBDOC_PUSH_PAT`** → в поле значения вставьте **PAT** (classic: scope **`repo`** для приватных репо и работы с форками; либо **fine-grained**: доступ к `ydb-platform/ydb`, **Contents** read/write и при необходимости **Pull requests** read).
3. Сохраните. В workflow должно быть `GITHUB_PUSH_TOKEN: ${{ secrets.YDBDOC_PUSH_PAT }}` (см. [`examples/ydb-github-doc-translate-on-label.yml`](examples/ydb-github-doc-translate-on-label.yml)).
4. Для **PR из форка** в условии `if:` уберите проверку `head.repo.fork == false`, иначе job не запустится.
5. Когда появится бот-аккаунт: создайте для него PAT с теми же правами, **обновите** значение секрета **`YDBDOC_PUSH_PAT`** и **отзовите** старый личный PAT в [настройках токенов](https://github.com/settings/tokens).

---

## CLI (кратко)

```bash
export PYTHONPATH="$(pwd)/src"
python -m ydbdoc_review list-models
python -m ydbdoc_review run --repo <owner>/<repo> --pr <N> \
  [--repo-path <путь>] [--merge-base-with origin/main] \
  [--dry-run] [--no-commit] [--no-push] [--no-comment]
python -m ydbdoc_review verify --repo <owner>/<repo> --pr <N> \
  [--repo-path <путь>] [--merge-base-with origin/main] \
  [--no-commit] [--no-push] [--no-comment] [--source-pr <N>]
```

Аргумент **`--repo`** — репозиторий, **в котором открыт PR** (например `ydb-platform/ydb`). Команда **`verify`** требует **`translation_self_check`** в config и checkout **head** проверяемого PR.

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

Workflow с вызовом action **выполняется в том репозитории, где лежит YAML** — у вас это **`ydb`**. Секреты **`YANDEX_CLOUD_*_DOC_REVIEW`** (и при необходимости **`YDBDOC_PUSH_PAT`**) заводятся в **настройках репозитория `ydb`**:

**`ydb` на GitHub** → **Settings** → **Secrets and variables** → **Actions** → **New repository secret** — для ydbdoc-review заведите **`YANDEX_CLOUD_FOLDER_DOC_REVIEW`**, **`YANDEX_CLOUD_API_KEY_DOC_REVIEW`** и при работе с **форками** — **`YDBDOC_PUSH_PAT`** (PAT для push; см. раздел «Push в форк» выше). Старые имена `YANDEX_CLOUD_*` без суффикса по-прежнему поддерживаются в коде и в `.env`.

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
| `dry_run` | `"true"` → флаг `--dry-run` (только для `mode: run`). |
| `no_commit` | `"true"` → `--no-commit` (`run`: без коммита перевода; `verify`: без commit/push правок критика). |
| `mode` | `run` (по умолчанию) — перевод по лейблу **`doc_translate`**. `verify` — self-check по лейблу **`doc_verify`**. |

### Запуск по лейблу `doc_verify` (проверка translation PR)

На **PR с переводом или bilingual** (ветка `ydbdoc-review/pr-<N>`, или PR с правками RU+EN): повесьте лейбл **`doc_verify`**.

Пример workflow: [`examples/ydb-github-doc-verify-on-label.yml`](examples/ydb-github-doc-verify-on-label.yml).

Что делает `mode: verify`:

- Пары RU↔EN из diff PR, где **оба файла есть на ветке PR** (хотя бы RU или EN в diff).
- **SOURCE (RU)** и **TRANSLATION (EN)** — с **ветки этого PR** (если в PR менялись и RU, и EN — сравниваются друг с другом).
- Тот же цикл, что после `doc_translate`: **критик → repair (правки EN) → переводчик** (`translation_repair` в config).
- При успехе repair — **коммит и push в ветку PR** (отключить: `no_commit` / `INPUT_NO_COMMIT` в action).
- Quality gate + вердикт **ПРИНЯТЬ / ОТКЛОНИТЬ**; при отклонении job падает.

Номер связанного doc PR (из заголовка translation PR) — только в комментарии; для diff scope используется **этот** PR.

Локально:

```bash
python -m ydbdoc_review verify \
  --repo ydb-platform/ydb \
  --pr 40631 \
  --repo-path /path/to/ydb \
  --merge-base-with origin/main
# без commit/push правок критика:
#   ... --no-commit
```

В workflow для verify нужны **`permissions: contents: write`** (commit repair) и при форке — **`YDBDOC_PUSH_PAT`** → `GITHUB_PUSH_TOKEN`, как для `doc_translate`.

После `actions/checkout` обязательно **`fetch-depth: 0`** и `git fetch` базовой ветки PR, иначе `merge-base` не найдётся. В **`merge_base_with`** передавайте тот же ref, что подтянули (например `origin/${{ github.event.pull_request.base.ref }}`); жёсткий `origin/main` ломает PR в другую базовую ветку.

Action собирается из **Dockerfile**: внутри контейнера репозиторий смонтирован в **`GITHUB_WORKSPACE`** (обычно `/github/workspace`). Переменная **`YDBDOC_REPO_PATH: ${{ github.workspace }}`** на раннере указывает на путь вида `/home/runner/...`, которого в контейнере нет — `entrypoint.sh` подменяет такой путь на **`GITHUB_WORKSPACE`**, так что можно оставить `github.workspace` как в примере. Там же выставляется **`git config safe.directory`** для смонтированного каталога (иначе Git 2.35+ даёт *dubious ownership*; у checkout `.git` иногда **файл**, не каталог — проверка в `entrypoint` через `-e`). В образе также задан **`safe.directory *`** для CI.

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
          GITHUB_PUSH_TOKEN: ${{ secrets.YDBDOC_PUSH_PAT }}  # секрет YDBDOC_PUSH_PAT → env для приложения
          YDBDOC_REPO_PATH: ${{ github.workspace }}
```

**PR из форка:** задайте секрет **`YDBDOC_PUSH_PAT`** (см. выше), уберите в `if:` проверку `fork == false`, включите у автора PR **Allow edits by maintainers**. Или отключите job (`if:`), если push в форк не нужен.

### Секреты и variables в репозитории

| Secret | Пример |
|--------|--------|
| `YANDEX_CLOUD_FOLDER_DOC_REVIEW` | ID каталога (`b1…`) |
| `YANDEX_CLOUD_API_KEY_DOC_REVIEW` | строка API-ключа FM |
| `YDBDOC_PUSH_PAT` | Необязательно: PAT для `git push` в ветку PR (нужен для **форка**). В `env` передаётся как `GITHUB_PUSH_TOKEN: ${{ secrets.YDBDOC_PUSH_PAT }}`. Временно — ваш PAT, затем бота. |

В `env` workflow передаются **имена переменных**, которые читает приложение; имена **секретов в GitHub** могут совпадать с ними, как в примере выше. Допустимы и старые имена секретов с маппингом, например `YANDEX_CLOUD_FOLDER: ${{ secrets.YANDEX_CLOUD_FOLDER }}` — код примет и их (см. таблицу локальных переменных в начале README).

| Variable | Пример |
|----------|--------|
| `YDBDOC_REVIEW_ENABLED` | `true` / `false` — глобально включить или выключить шаг (пустое = как в `ydbdoc-review.toml`). |
| `YDBDOC_MODEL_CHECK` | Необязательно, если заданы модели в `ydbdoc-review.toml` в образе/репо или устраивают встроенные значения. |
| `YDBDOC_MODEL_TRANSLATE` | То же; иначе переопределение slug для перевода. |
| `YDBDOC_TRANSLATION_SELF_CHECK` | Необязательно: `true` / `false` — переопределить **`[feature].translation_self_check`** в TOML образа (пустое = только TOML). |
| `YDBDOC_MODEL_TRANSLATION_VERIFY` | Необязательно: переопределить **`[models].translation_verify`** в TOML. |
| `YDBDOC_TRANSLATE_MAX_OUTPUT_TOKENS` | Потолок выходных токенов **перевода**. По умолчанию **1048576** (1M; шлюз провайдера может отрезать ниже). **`0`** — то же 1M. Задайте меньшее число, если API возвращает ошибку на слишком большой `max_tokens`. |
| `YDBDOC_ANALYZE_TRUNCATE_CHARS` | Необязательно: положительное число — усечь `ru_text`/`en_text` в **каждом** батче анализа. Пусто или **`0`** — полные тела (батчинг по `YDBDOC_ANALYZE_MAX_JSON_CHARS`). |
| `YDBDOC_ANALYZE_DIFF_MAX` | Необязательно: макс. длина `ru_diff_vs_base` / `en_diff_vs_base` в JSON анализа (по умолчанию **500000**). |
| `YDBDOC_ANALYZE_MAX_JSON_CHARS` | По умолчанию **24000**: макс. размер `json.dumps({"pairs":…})` **на один** вызов check-модели; пары разбиваются на несколько запросов. Если одна пара не влезает — для неё поля укорачиваются (редкий случай). |

Встроить job можно рядом с существующим `build-docs` в `.github/workflows/docs_build.yaml`, указав `uses:` на ваш тег релиза этого action или на vendored-путь (`./tools/ydbdoc-review`).

---

## Как это работает

1. **Список изменённых файлов:** GitHub API «files changed» в PR **или** локально `git merge-base` + `git diff --name-only`.
2. **Пары:** пути `ydb/docs/ru/…` ↔ `ydb/docs/en/…` с тем же хвостом (корень задаётся **`DOCS_SRC_ROOT`**, по умолчанию `ydb/docs`).
3. **Проверка:** несколько вызовов «дешёвой» check-модели при необходимости: пары документов режутся на **батчи**, чтобы каждый JSON `{"pairs":[...]}` не превышал **`YDBDOC_ANALYZE_MAX_JSON_CHARS`** (по умолчанию **24000** символов). В батч попадают **полные** `ru_text`/`en_text`, если не задано **`YDBDOC_ANALYZE_TRUNCATE_CHARS`**. При локальном git в JSON добавляются **`ru_diff_vs_base`** и **`en_diff_vs_base`** (лимит длины — **`YDBDOC_ANALYZE_DIFF_MAX`**, по умолчанию большой). Полный unified diff по-прежнему используется локально для эвристик (override RU-only diff).
4. **Перевод:** «дорогая» модель вызывается только если для пары указано `needs_generation_for`: `en` или `ru`. При **локальном** checkout (`--repo-path` / `YDBDOC_REPO_PATH`) и успешном `git merge-base` перевод идёт в **безопасном режиме**: в модель передаётся **unified diff** исходного языка между merge-base и `HEAD` (все коммиты PR в этой ветке, не «только последний») и **полный файл целевого языка как эталон** — промпты [`03_translate_ru_diff_to_en.txt`](prompts/03_translate_ru_diff_to_en.txt) / [`04_translate_en_diff_to_ru.txt`](prompts/04_translate_en_diff_to_ru.txt). Если diff посчитать нельзя, diff-вызов модели падает или нет эталона на ветке (например, ещё нет EN) — используется **полный** перевод исходного файла. Без локального репозитория (только API списка файлов + клон head) diff недоступен — снова полный файл.
5. **Git:** коммит на ветке **`ydbdoc-review/pr-<номер_исходного_PR>`** (исходная ветка PR **не** меняется); **push** этой ветки в **head** репозиторий; открытие **нового PR** (`head` = ветка перевода, `base` = `head.ref` исходного PR).
6. **Комментарий в исходном PR:** ссылка на PR перевода, список EN-файлов, которых **не было на merge-base** (нужен полный перевод и сначала мерж отдельного PR), и файлов, где EN уже был (после мержа можно снова повесить `doc_translate` для обновления по diff).

### Порядок для авторов (реальный репозиторий)

1. На PR с правками RU вешаете **`doc_translate`**.
2. Появляется **отдельный PR** с переводом — **не** смешивайте его с исходным PR документации.
3. Если в комментарии указано, что EN **не существовал на merge-base**, переведён **весь** файл; ссылки на другие страницы, которых ещё нет в `en/`, могут сломать билд — переведите недостающие RU-страницы **другими** PR (с тем же лейблом), смержите их **раньше**.
4. **Смержите** PR перевода (или залейте ветку в базу), **обновите** ветку исходного PR.
5. При необходимости снова **`doc_translate`** на исходном PR — тогда EN уже есть, и обновление пойдёт **по diff** коммитов PR.

Автоматический обход истории git («в каких PR появился файл») и разбор ссылок в markdown **пока не делаются** — только предупреждения в комментариях.

## Ограничения

- Для проверки размер одного запроса к FM ограничен **`YDBDOC_ANALYZE_MAX_JSON_CHARS`** (несколько батчей). При слишком большой **одной** паре возможно точечное усечение в логе. При необходимости задайте **`YDBDOC_ANALYZE_TRUNCATE_CHARS`** или разбейте PR.
- Если check-модель (Yandex FM) вместо JSON вернула **отказ** («не могу обсуждать эту тему» и т.п.) или обрыв: для батча выполняется **повтор** с укороченным телом запроса; при повторной неудаче — **эвристический fallback** по diff/списку изменённых файлов, чтобы job не падал; такие пары стоит перепроверить вручную.
- Дифф для перевода усечён по умолчанию **`YDBDOC_MAX_DIFF_CHARS`** (120000 символов); при очень больших правках RU/EN режим diff может быть неполным.
- Лимит **выходных** токенов перевода — **`YDBDOC_TRANSLATE_MAX_OUTPUT_TOKENS`** (по умолчанию **1048576**; провайдер может применить свой потолок). При подозрении на обрыв делается **второй** вызов с удвоенным лимитом (до того же потолка в коде). Если API ругается на значение — уменьшите вручную.
- Даже в diff-режиме модель **не гарантирует** побайтовое совпадение неизменных фрагментов с эталоном; промпт снижает риск (лишние `./`, пустые строки, «улучшение» ссылок). Критичные PR проверяйте диффом вручную.
- Если оба языка есть, но расходятся по смыслу, инструмент **не перезаписывает** файлы автоматически — в комментарии будет блок про ручной разбор.
- Перевод **не коммитится** в ветку исходного PR; транзитивные зависимости по ссылкам и цепочка «сначала старые PR» — **вручную**, по подсказкам в комментарии.
- Запись `.md` через `write_text` **нормализует** окончание файла: один завершающий перевод строки после удаления хвостовых пустых строк, чтобы Git не показывал ложное изменение последней строки из‑за отсутствия newline в конце файла.

## Лицензия

При публикации на GitHub задайте лицензию явно (например Apache-2.0 в духе YDB или другую по выбору владельца репозитория).
