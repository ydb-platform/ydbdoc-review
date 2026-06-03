# Memory Bank — LLM, config & prompts

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

---

## 12. LLM details (Yandex AI Studio)

### 12.1. Endpoint

```
https://ai.api.cloud.yandex.net/v1
```

This is the **OpenAI-compatible** endpoint, as per official Yandex AI Studio
documentation. (There is also a legacy `llm.api.cloud.yandex.net` and a
non-OpenAI `foundationModels/v1/chatCompletion`. We use the AI Studio one.)

### 12.2. Auth

- API key (a.k.a. "Secret key" in UI), starts with `AQVN...`.
- Header: `Authorization: Api-Key <key>`. The OpenAI SDK builds this when
  given `api_key=...`.
- IAM tokens also work but are short-lived. We use API key.

### 12.3. Model URI format

```
gpt://<folder_id>/<model_slug>
```

E.g. `gpt://b1gj42mhlf663vd7slc2/yandexgpt-5.1`.

### 12.4. Available models (as of this writing)

| Slug | Context | Notes |
|---|---|---|
| `yandexgpt-5.1` | 32k | YandexGPT Pro 5.1 |
| `yandexgpt-5-pro` | 32k | Older Pro |
| `yandexgpt-5-lite` | 32k | Cheaper Lite |
| `deepseek-v32` | 128k | DeepSeek V3.2 (V4 rolling out, will replace) |
| `qwen3.6-35b-a3b` | 256k | Qwen 3.6 35B — for critic |
| `qwen3-235b-a22b-fp8` | 256k | Qwen 3 235B — heavier alt |
| `gpt-oss-120b` | 128k | OpenAI OSS 120B |
| `gpt-oss-20b` | 128k | OpenAI OSS 20B |
| `gemma-3-27b-it` | 128k | Available until May 2026 |
| `aliceai-llm` | 32k | Alice AI LLM |

### 12.5. Smoke test results (verified)

Both `yandexgpt-5.1` and `deepseek-v32` were tested with:
1. Plain RU→EN translation prompt. Both produced correct output.
2. JSON I/O prompt asking for `{"translations": [...]}`.
   - **yandexgpt-5.1**: returns JSON, but wraps in ` ``` ``` ` fences. Parser
     must strip code fences. Placeholder `⟦C1⟧` preserved.
   - **deepseek-v32**: returns clean JSON, no fences. Placeholder preserved.

### 12.6. Known limitations

- ❌ `response_format={"type":"json_object"}` — NOT supported.
- ❌ Function/tool calling — NOT supported.
- ⚠️ `top_p` — may be ignored.
- ✅ `temperature`, `max_tokens`, `messages`, `model`, `stream` — supported.

### 12.7. Model selection (v2 MVP)

| Role | Primary | Fallbacks | Rationale |
|---|---|---|---|
| **Pre-analyze** | `yandexgpt-5-lite` | `yandexgpt-5.1` | Lightweight binary classification |
| **Translator** | `yandexgpt-5.1` | `yandexgpt-5-pro` | Familiar baseline; switch to DeepSeek 4 in prod when available |
| **Critic** | `qwen3.6-35b-a3b` | `qwen3-235b-a22b-fp8` | Different family from translator; large context for whole-file view |

When DeepSeek V4 is available in AI Studio: switch translator primary to
`deepseek-v4` (slug TBD); keep YandexGPT as fallback.

---

---

## 13. Configuration

### 13.1. File location

```
src/ydbdoc_review/config/default.yaml
```

Bundled with the package. Loaded by `config/loader.py`.

### 13.2. Schema (implemented in `loader.py`)

```yaml
llm:
  provider: yandex
  base_url: https://ai.api.cloud.yandex.net/v1
  # Folder ID and API key come from env vars only (see §13.4).

  temperature: 0.1
  max_tokens: 8000
  timeout_s: 120

  retries:
    max_attempts: 3
    backoff_initial_s: 2.0
    backoff_factor: 2.0

  concurrency:
    batches_per_file: 3

  models:
    analyze:
      primary: yandexgpt-5-lite
      fallbacks: [yandexgpt-5.1]
    translate:
      primary: yandexgpt-5.1
      fallbacks: [yandexgpt-5-pro]
    critic:
      primary: qwen3.6-35b-a3b
      fallbacks: [qwen3-235b-a22b-fp8]

translation:
  segments_per_batch_chars: 4000
  source_lang: ru
  target_lang: en

prompts:
  version: v1
  glossary_path: prompts/glossary.yaml

paths:
  docs_root: ydb/docs
  translation_branch_prefix: ydbdoc-review/pr-

reporting:
  include_cost: true
  include_token_usage: true
  include_heuristics: true
```

### 13.3. Env-var override

Convention: prefix `YDBDOC_`, then YAML path with **dots and snake_case field
names → underscores**. Nested dict keys are single segments; multi-word field
names stay one segment (e.g. `max_tokens`, not `max` + `tokens`).

Resolution (`loader._resolve_config_path`): at each YAML level, greedily join
the longest prefix of remaining env segments that matches an existing key.
Unknown paths are **ignored** (no crash, no `extra` validation error).
Secret vars (`YDBDOC_YC_*`, `YDBDOC_PUSH_*`) are routed to `_resolve_secrets`,
not config overrides.

Examples:
- `YDBDOC_LLM_TEMPERATURE=0.2` → `llm.temperature`
- `YDBDOC_LLM_MAX_TOKENS=16000` → `llm.max_tokens`
- `YDBDOC_LLM_BASE_URL=https://…/v1/` → `llm.base_url` (trailing slash stripped)
- `YDBDOC_REPORTING_INCLUDE_COST=false` → `reporting.include_cost`
- `YDBDOC_LLM_MODELS_TRANSLATE_PRIMARY=deepseek-v4` → `llm.models.translate.primary`
- `YDBDOC_LLM_MODELS_TRANSLATE_FALLBACKS=gpt-oss-120b, deepseek-v32` → CSV list
- `YDBDOC_TRANSLATION_SEGMENTS_PER_BATCH_CHARS=2000` → `translation.segments_per_batch_chars`
- `YDBDOC_FOO_BAR=baz` → ignored (no such path in default YAML)

### 13.4. Secrets (env only, never in YAML)

Order of precedence:
1. `YDBDOC_YC_FOLDER_ID`, `YDBDOC_YC_API_KEY`  — preferred new names.
2. `YANDEX_CLOUD_FOLDER_DOC_REVIEW`, `YANDEX_CLOUD_API_KEY_DOC_REVIEW` — v1 compat.
3. `YANDEX_CLOUD_FOLDER`, `YANDEX_CLOUD_API_KEY` — generic.
4. `YANDEX_CLOUD_FOLDER_2`, `YANDEX_CLOUD_SECRET_KEY` — current user's bashrc.

All four pairs supported simultaneously; first found wins.

GitHub: **`GITHUB_TOKEN`** in CI (job token; see **07-pipeline** §16.7). Optional
`GITHUB_PUSH_TOKEN` / `YDBDOC_PUSH_PAT` only if push with job token fails. Translation
branches push to upstream, not contributor forks.

### 13.5. `.env.example` (committed)

```
# Yandex AI Studio
YDBDOC_YC_FOLDER_ID=
YDBDOC_YC_API_KEY=

# Optional model overrides
# YDBDOC_LLM_MODELS_TRANSLATE_PRIMARY=
# YDBDOC_LLM_MODELS_CRITIC_PRIMARY=

# GitHub (local PR operations; in ydb CI use job GITHUB_TOKEN only)
# GITHUB_TOKEN=
# GITHUB_PUSH_TOKEN=   # optional; omit in ydb workflows unless push 403
```

User copies to `.env` and fills in. `.env` is gitignored.

---

---

## 14. Glossary

### 14.1. Source of truth

`src/ydbdoc_review/prompts/glossary.yaml` — committed, hand-maintained for now.
Loaded by `translation/glossary.py` (`load_glossary()`, `Glossary.to_prompt_yaml()`).

### 14.2. Format

```yaml
- ru: "параметризованный запрос"
  en: "parameterized query"
  aliases_ru: ["параметризованные запросы", "параметризованного запроса"]
  notes: "Always lowercase."

- ru: "узел"
  en: "node"
  aliases_ru: ["узла", "узлу", "узлы"]
  context: "YDB cluster topology"

- ru: "{{ ydb-short-name }}"
  en: "{{ ydb-short-name }}"
  do_not_translate: true
  notes: "YFM variable; literal."

- term: "YDB"
  do_not_translate: true

- term: "SQL"
  do_not_translate: true
```

Entries with `do_not_translate: true` apply to both languages.

### 14.3. Injection (MVP: full)

The entire glossary is included in every translator and critic prompt. Size
expected: ~30-50 entries seed, ~100-200 mature, well below 32k context limit.

### 14.4. Future: relevant-subset injection

Optimization for tokens: scan batch text for any glossary term/alias, include
only matching entries. Tracked in backlog.

### 14.5. Sync from YDB

Future: `scripts/refresh_glossary.py` parses
`https://ydb.tech/docs/ru/concepts/glossary?version=main` and proposes a diff.
Manual maintenance for now.

---

---

## 18. Prompts

### 18.1. Versioning

```
src/ydbdoc_review/prompts/
├── v1/
│   ├── system_common.md       Shared system instructions + glossary injection
│   ├── translate.md           Translator user prompt (batch JSON)
│   ├── critic.md              Critic prompt template
│   ├── verify.md              Verify pass prompt template
│   ├── analyze.md             Pre-analyze prompt template
│   └── en_style_guide.md      EN style rules (injected for ru→en translate)
└── glossary.yaml              Glossary (shared across versions)
```

Each prompt template is markdown with `{placeholders}` filled at runtime by
`translation/prompts.py` (`load_template`, `render_template`, `build_*_messages`).

Public builders:
- `build_translate_messages(batch, glossary, file_path=…)` — segment JSON I/O
- `build_critic_batch_messages(…)` / `build_verify_batch_messages(…)` — batched review
- `build_critic_messages(…)` / `build_verify_messages(…)` — legacy whole-file (unused)
- `build_analyze_messages(pairs, glossary)` — PR pre-analyze

### 18.2. Versioning policy

- Current version recorded in `config/default.yaml` → `prompts.version`.
- Old versions kept indefinitely for reproducibility.
- New version (`v2/`) created when behavior changes are non-trivial.
- The report footer always includes the prompt version used.

### 18.3. Common system instructions

Implemented in `prompts/v1/system_common.md` (glossary via `{glossary_yaml}`).
The **PLACEHOLDERS** block tells the model that `⟦X{n}⟧` tokens are opaque:
translate only prose between them; never expand to `{{ var }}`, URLs, or `` `code` ``.
User prompt `translate.md` repeats link shape `[anchor](⟦U{n}⟧)` and no reordering.
Translator batch schema: `{"segments": [{"id", "kind", "path", "text"}, …]}` →
response `{"segments": [{"id", "text"}, …]}`.

---

---

[← Memory Bank index](../../MEMORY_BANK.md)
