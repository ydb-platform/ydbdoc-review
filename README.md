# ydbdoc-review

Утилита для CI и командной строки: по pull request в документации YDB находит пары статей **русский ↔ английский** (`.md` под `ydb/docs/`). Дешёвой моделью определяет, какие файлы нужно переводить, переводит их **посегментно** (заголовки, прозу, таблицы, fenced-блоки, `{% note %}` / `{% cut %}` — отдельными вызовами), потом запускает **QA**: критик сравнивает RU↔EN, при «НЕ ПРИНИМАТЬ» возвращает точечный fix-diff, переводчик повторно проверяет результат, в конце применяются **эвристики** (длина, кириллица, fence-баланс, паритет SDK-вкладок).

Исходную ветку PR утилита **не трогает**: перевод и исправления коммитятся в отдельную ветку `ydbdoc-review/pr-<N>`, открывается **отдельный PR**, в исходный PR уходит **комментарий** со ссылкой на translation PR и сводный отчёт с вердиктом. **Коммит создаётся всегда**, CI всегда зелёный (кроме инфраструктурных ошибок) — решение о мерже translation PR за пользователем.

Отдельный лейбл **`doc_verify`** (`mode: verify`): на **translation PR** или **bilingual PR** (RU+EN в одной ветке) тот же QA, что после автоперевода; RU и EN берутся **с ветки этого PR** (не с `main`).

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

**Переводчик по умолчанию:** **`deepseek-v4-flash/latest`** (DeepSeek-V4-Flash в [Model gallery](https://aistudio.yandex.ru/model-gallery); модель должна быть **включена** для folder). В FM уходит `gpt://<folder>/deepseek-v4-flash/latest` (folder из `YANDEX_CLOUD_FOLDER_DOC_REVIEW`) или задайте полный URI: **`YDBDOC_MODEL_TRANSLATE=gpt://<folder>/deepseek-v4-flash/latest`**. Если slug недоступен (`Failed to get model`), пробуются **`YDBDOC_MODEL_TRANSLATE_FALLBACKS`** (`yandexgpt-5.1`, `yandexgpt/latest`). Критик (`translation_verify`, Qwen) **не трогайте**. Локально один файл:

```bash
cp .env.example .env   # ключи FM
PYTHONPATH=src python scripts/translate_one_file.py \
  --source debug/pqe-source-ru.md \
  --out debug/pqe-en-yandex.md \
  --model yandexgpt-5.1
PYTHONPATH=src python scripts/translate_one_file.py \
  --source debug/pqe-source-ru.md \
  --out debug/pqe-en-deepseek.md \
  --model deepseek-v4-flash/latest
diff -u debug/pqe-en-yandex.md debug/pqe-en-deepseek.md | less
```

**QA pipeline** (TOML: **`translation_self_check`**, **`translation_repair`**; модель-критик — **`[models].translation_verify`**): на каждый файл максимум три «тяжёлых» FM-запроса.

1. **Compare** — критик сравнивает RU↔EN по `prompts/05_verify_translation.txt`, возвращает вердикт `ПРИНИМАТЬ` / `ПРИНИМАТЬ С ОГОВОРКАМИ` / `НЕ ПРИНИМАТЬ` с разделами «Блокеры» и «Оговорки».
2. **Fix-diff** — только при `НЕ ПРИНИМАТЬ`. Критик по `prompts/06_fix_translation.txt` возвращает JSON `{"fixes":[{find,replace,reason}]}`; CLI применяет точечные `str.replace`. Если `find` не найден или встречается несколько раз — fix пропускается, попадает в отчёт. Никаких полных перезаписей файла.
3. **Re-validate** — только если хоть один fix применился. Переводчик по `prompts/07_confirm_repair.txt` проверяет результат тем же шаблоном вердикта.
4. **Эвристики** — всегда. Список правил в `prompts/09_quality_heuristics.md`: кириллица в EN, расхождение длины >25%, баланс ` ``` ` и `{% %}`, паритет `## / ###`, паритет `{% list tabs %}`, целые непереведённые секции (через LLM).

Отключить шаг fix: **`translation_repair = false`** или **`YDBDOC_TRANSLATION_REPAIR=false`** — останутся только compare и эвристики. `doc_verify` использует **тот же** код QA на ветке PR; правки критика пушатся в эту же ветку (отключить: `no_commit: "true"` / `--no-commit`).

**Перевод по сегментам**: текст файла разбирается на `prose`/`table`/`fence`/`diplodoc`-блоки (новый prose-блок на каждом `###`); каждый unit — отдельный FM-запрос. Инструкции собирает **`PromptBuilder`**: иерархия Best/Satisfactory/Unacceptable (`prompts/translate_quality_hierarchy.md`), для EN — style guide (`prompts/en_style_guide.md`), опционально глоссарий (`YDBDOC_GLOSSARY_PATH` / `[prompts].glossary`), правила фрагмента — `prompts/08_translate_segment.txt`.

**Другой провайдер (не Yandex):** задайте **`OPENAI_BASE_URL`** (или `YDBDOC_LLM_BASE_URL`) на корень совместимого API и **`OPENAI_API_KEY`** (или `YDBDOC_LLM_API_KEY`). Каталог Yandex не нужен. В `ydbdoc-review.toml` или в env укажите **ид модели в формате этого провайдера** (например `gpt-4o-mini`, `gpt-4o`). Для Yandex FM по-прежнему можно задать полный URI в `gpt://…` в TOML — тогда префикс каталога не добавляется автоматически.

Список id моделей в вашем каталоге (если шлюз отдаёт `GET /v1/models`):

```bash
export PYTHONPATH="$(pwd)/src"
python -m ydbdoc_review list-models
```

Официальный перечень в интерфейсе: [Yandex AI Studio — Model gallery](https://aistudio.yandex.ru/model-gallery). Для критика по умолчанию: **`qwen3.6-35b-a3b`** (`[models].translation_verify`). Переопределение: **`YDBDOC_MODEL_TRANSLATION_VERIFY`** или `vars` в workflow `ydb`. При `Failed to get model` пробуются **`YDBDOC_MODEL_VERIFY_FALLBACKS`** (по умолчанию `qwen3-235b-a22b/latest`, `deepseek-v3.2/latest` — намеренно **не Yandex**, чтобы критик не оказался той же семьёй, что и переводчик).

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

- Тексты промптов лежат в каталоге [`prompts/`](prompts/):
  - [`01_analyze_translation_pairs.txt`](prompts/01_analyze_translation_pairs.txt) — alignment-check.
  - [`05_verify_translation.txt`](prompts/05_verify_translation.txt) — критик: вердикт `ПРИНИМАТЬ` / `ПРИНИМАТЬ С ОГОВОРКАМИ` / `НЕ ПРИНИМАТЬ` + блокеры/оговорки.
  - [`06_fix_translation.txt`](prompts/06_fix_translation.txt) — fix-diff в формате JSON `{find,replace,reason}`.
  - [`07_confirm_repair.txt`](prompts/07_confirm_repair.txt) — повторная проверка после fix.
  - [`08_translate_segment.txt`](prompts/08_translate_segment.txt) — перевод одного unit-а файла.
  - [`09_quality_heuristics.md`](prompts/09_quality_heuristics.md) — декларативный список эвристик (детерминированные + LLM).
- Их можно править без изменения кода; каталог можно переопределить: **`YDBDOC_PROMPTS_DIR`**.
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
- **SOURCE (RU)** и **TRANSLATION (EN)** — с **ветки этого PR**.
- Тот же QA, что после `doc_translate`: **compare → fix-diff (при «НЕ ПРИНИМАТЬ») → re-validate → эвристики**.
- Если хоть один fix-diff применился — **коммит и push в ветку PR** (отключить: `no_commit` / `INPUT_NO_COMMIT` в action).
- Отчёт со всеми вердиктами и эвристиками — комментарий в PR.
- **Job всегда зелёный**, кроме инфраструктурных ошибок (FM API полностью лёг, нет прав на push, баг в коде). Никакого gate на «НЕ ПРИНИМАТЬ»: коммит появляется, пользователь читает отчёт и решает.

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
| `YDBDOC_MODEL_TRANSLATE_FALLBACKS` | Через запятую, если primary недоступен в FM (`Failed to get model`). По умолчанию `yandexgpt-5.1`, `yandexgpt/latest`. |
| `YDBDOC_TRANSLATION_SELF_CHECK` | Необязательно: `true` / `false` — переопределить **`[feature].translation_self_check`** в TOML образа (пустое = только TOML). |
| `YDBDOC_TRANSLATION_REPAIR` | Необязательно: `false` — выключить шаг fix-diff (останется только compare + эвристики). |
| `YDBDOC_MODEL_TRANSLATION_VERIFY` | Необязательно: переопределить **`[models].translation_verify`** в TOML. |
| `YDBDOC_MODEL_VERIFY_FALLBACKS` | Через запятую: какие модели пробовать, если основной критик недоступен. По умолчанию `qwen3-235b-a22b/latest, deepseek-v3.2/latest` — не семья Yandex. |
| `YDBDOC_GLOSSARY_PATH` | Необязательно: `.md` с терминами для перевода (см. `prompts/glossary.example.md`). |
| `YDBDOC_PROJECT_INFO_PATH` | Необязательно: краткое описание проекта/доки в system prompt. |
| `YDBDOC_PROMPTS_DIR` | Каталог с `08_translate_segment.txt`, style guide и иерархией качества (по умолчанию `prompts/` в пакете). |
| `YDBDOC_QA_MAX_INPUT_CHARS` | Cap входных символов для compare/fix-вызовов (по умолчанию **55000**). |
| `YDBDOC_QA_MAX_OUTPUT_TOKENS` | Cap выходных токенов QA-вызовов (по умолчанию **16384**). |
| `YDBDOC_HEURISTICS_MAX_INPUT_CHARS` | Cap для LLM-вызова эвристик из `prompts/09_quality_heuristics.md` (по умолчанию **40000**). |
| `YDBDOC_MODEL_COMPLETION_TOKEN_CEILING` | Жёсткий потолок completion-токенов (по умолчанию **1048576**; для DeepSeek/Qwen FM код автоматически клампит до **32768**). |
| `YDBDOC_ANALYZE_TRUNCATE_CHARS` | Необязательно: положительное число — усечь `ru_text`/`en_text` в **каждом** батче анализа. Пусто или **`0`** — полные тела (батчинг по `YDBDOC_ANALYZE_MAX_JSON_CHARS`). |
| `YDBDOC_ANALYZE_DIFF_MAX` | Необязательно: макс. длина `ru_diff_vs_base` / `en_diff_vs_base` в JSON анализа (по умолчанию **500000**). |
| `YDBDOC_ANALYZE_MAX_JSON_CHARS` | По умолчанию **24000**: макс. размер `json.dumps({"pairs":…})` **на один** вызов check-модели; пары разбиваются на несколько запросов. Если одна пара не влезает — для неё поля укорачиваются (редкий случай). |
| `YDBDOC_FILE_TRANSLATE_MAX_CHARS` | По умолчанию **28000**: legacy annotated — макс. размер чанка SOURCE. |
| `YDBDOC_MASKED_CHUNK_CHARS` | Макс. размер одного masked-chunk для LLM (по умолчанию как `YDBDOC_FILE_TRANSLATE_MAX_CHARS`, **12000**). |
| `YDBDOC_PLACEHOLDER_BATCH_CHARS` | Для legacy line-JSON: макс. размер JSON-batch (**10000**). |
| `YDBDOC_LEGACY_LINE_JSON` | `true` — JSON по строкам (`12_translate_placeholder_json.txt`) вместо mask→unmask. |
| `YDBDOC_LEGACY_ANNOTATED` | `true` — annotated/file-chunk перевод вместо mask→unmask. |
| `YDBDOC_TRANSLATE_LEGACY_SEGMENTS` | `true` — старый пайплайн (отдельный LLM-вызов на каждый unit/tabs-blob). |

Встроить job можно рядом с существующим `build-docs` в `.github/workflows/docs_build.yaml`, указав `uses:` на ваш тег релиза этого action или на vendored-путь (`./tools/ydbdoc-review`).

---

## Как это работает

1. **Список изменённых файлов:** GitHub API «files changed» в PR **или** локально `git merge-base` + `git diff --name-only`.
2. **Пары:** пути `ydb/docs/ru/…` ↔ `ydb/docs/en/…` с тем же хвостом (корень задаётся **`DOCS_SRC_ROOT`**, по умолчанию `ydb/docs`).
3. **Analyze:** один или несколько вызовов «дешёвой» check-модели по [`prompts/01_analyze_translation_pairs.txt`](prompts/01_analyze_translation_pairs.txt). Возвращает по каждой паре: есть ли RU/EN, выровнены ли по смыслу, и `needs_generation_for: en|ru|null`. Пары режутся на батчи, чтобы каждый JSON `{"pairs":[...]}` не превышал **`YDBDOC_ANALYZE_MAX_JSON_CHARS`** (24000 символов по умолчанию).
4. **Translate:** для каждой пары, где `needs_generation_for ∈ {en, ru}`, файл разбирается на **регионы по номерам строк** (prose, table, fence, tabs, …) — см. `document_structure.py`. В промпт [`prompts/10_translate_file_with_plan.txt`](prompts/10_translate_file_with_plan.txt) передаётся **план** («строки 10–40: fence, не переводить; …») и исходник. Обычно **1 запрос LLM на файл**; если файл большой — **2–N запросов** с пометкой «запрос K из N». При малом PR-diff и наличии EN на `main` переводятся только затронутые секции `###`. Детерминированные пост-фиксы (ссылки, CLI). Старый segment-пайплайн: `YDBDOC_TRANSLATE_LEGACY_SEGMENTS=true`. Откат: тег **`pre-file-translate`** (`4bf42f4`), см. [ROLLBACK.md](ROLLBACK.md).
5. **QA:** на каждый сгенерированный файл — **compare** ([`05`](prompts/05_verify_translation.txt)) → **fix-diff** ([`06`](prompts/06_fix_translation.txt)) при «НЕ ПРИНИМАТЬ» → **re-validate** ([`07`](prompts/07_confirm_repair.txt)) если хоть один fix применился → **эвристики** ([`09`](prompts/09_quality_heuristics.md)). Максимум 3 «тяжёлых» FM-запроса на файл.
6. **Git:** коммит на ветке **`ydbdoc-review/pr-<номер_исходного_PR>`** (исходная ветка PR **не** меняется); **push** в **head**-репозиторий; открытие **нового PR** (`head` = ветка перевода, `base` = `head.ref` исходного PR). Коммит создаётся **всегда** независимо от вердикта QA.
7. **Комментарий в исходном PR:** ссылка на translation PR, краткая сводка по файлам. **Полный отчёт QA** (вердикты, блокеры, оговорки, пропущенные fixes, эвристики) — отдельным комментарием в **translation PR**.

### Порядок для авторов (реальный репозиторий)

1. На PR с правками RU вешаете **`doc_translate`**.
2. Появляется **отдельный PR** с переводом — **не** смешивайте его с исходным PR документации.
3. Если в комментарии указано, что EN **не существовал на merge-base**, переведён **весь** файл; ссылки на другие страницы, которых ещё нет в `en/`, могут сломать билд — переведите недостающие RU-страницы **другими** PR (с тем же лейблом), смержите их **раньше**.
4. **Смержите** PR перевода (или залейте ветку в базу), **обновите** ветку исходного PR.
5. При необходимости снова **`doc_translate`** на исходном PR — тогда EN уже есть, и обновление пойдёт **по diff** коммитов PR.

Автоматический обход истории git («в каких PR появился файл») и разбор ссылок в markdown **пока не делаются** — только предупреждения в комментариях.

## Ограничения

- **Masked-перевод (по умолчанию):** mask → translate → unmask. Fences / config tabs — **COPY**; в prose ссылки, HTML, `` `code` ``, `{{ var }}`, `{#anchor}` заменяются на `⟦KIND:n⟧`, LLM переводит только текст между плейсхолдерами (`prompts/13_translate_masked_document.txt`), Python восстанавливает атомы.
- **Legacy line-JSON:** `YDBDOC_LEGACY_LINE_JSON=true` — JSON-batch по строкам (`12_translate_placeholder_json.txt`).
- **Legacy annotated:** `YDBDOC_LEGACY_ANNOTATED=true` — чанки с REGION MAP.
- Малый PR-diff — только секции `###`, затронутые diff (остальное EN с `main`).
- **QA-критик и переводчик не должны быть из одной семьи моделей.** Дефолтные fallbacks критика — `qwen3-235b-a22b/latest`, `deepseek-v3.2/latest`. Если переопределяете `YDBDOC_MODEL_VERIFY_FALLBACKS`, не ставьте туда `yandexgpt*` — критик потеряет независимость от переводчика.
- **Fix-diff применяется только при точном совпадении.** Если модель-критик в поле `find` ошиблась хотя бы в одном символе или вернула неуникальный фрагмент — fix пропускается, в отчёт пишется причина. Это безопасно: ничего лишнего не правится, но иногда блокер остаётся неисправленным до следующего запуска `doc_translate`.
- **Эвристика «секция не переведена» — LLM-вызов.** Если модель для эвристик недоступна, эта проверка молча пропускается; детерминированные эвристики (длина, кириллица, fences, headings, list tabs, liquid tags) продолжают работать.
- **Аnalyze-батчи усекают полный текст.** Размер одного запроса к check-модели ограничен `YDBDOC_ANALYZE_MAX_JSON_CHARS`. При большом одиночном файле его поля укорачиваются в логе; перепроверьте вердикт alignment вручную.
- **Если check-модель вернула отказ** («не могу обсуждать эту тему») или обрыв — батч повторяется с укороченным телом; при повторной неудаче — эвристический fallback по diff/changed-files, чтобы job не падал; такие пары стоит проверить руками.
- **CI всегда зелёный.** Решение о мерже translation PR — за пользователем. Если хочется автоматического gate — обернуть `gh pr view` поверх результатов QA-комментария в отдельном workflow.
- **Транзитивные зависимости по ссылкам** (на ещё не переведённые страницы) **не обрабатываются** автоматически — только предупреждения в комментарии.
- **Запись `.md`** нормализует окончание файла: один завершающий перевод строки после удаления хвостовых пустых строк, чтобы Git не показывал ложное изменение последней строки.

## Лицензия

При публикации на GitHub задайте лицензию явно (например Apache-2.0 в духе YDB или другую по выбору владельца репозитория).
