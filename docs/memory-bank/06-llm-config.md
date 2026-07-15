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
  critic_feedback_retries: 2
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
- `YDBDOC_TRANSLATION_CRITIC_FEEDBACK_RETRIES=0` → disable critic-guided retranslate (default `2`)
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

## 13.6. Alternate provider: Eliza (internal, OpenAI-compatible)

During migration, `ydbdoc-review` supports two model providers behind the same
pipeline behavior. Switching provider changes **only** transport/auth/model id;
prompts, merge-base diff logic, file writes, commit/push, and reports stay the same.

Default remains **`YDBDOC_MODEL_PROVIDER=yandex_cloud`** — GitHub Actions workflows
in `ydb` need no changes. Eliza is enabled only by setting env on the child process.

### 13.6.1. Provider selector

- Env: `YDBDOC_MODEL_PROVIDER`
  - `yandex_cloud` — default, Yandex AI Studio (`gpt://<folder>/<slug>`)
  - `eliza` — internal Eliza HTTP transport (§13.6.2)

Factory: `create_llm_client(config)` in `llm/client.py`.

### 13.6.2. Endpoint route (model in URL path)

Internal models **must not** use the legacy OpenAI-compat route
`{root}/raw/openai/v1` with `model` in the JSON body — that fails with
`model … is not available for vendor "openai"`.

Correct route (prefix `/raw` is mandatory):

```
POST {ELIZA_API_ROOT}/raw/internal/{model_id}/v1/chat/completions
```

- `ELIZA_API_ROOT` — env, default `https://api.eliza.yandex.net`
- `model_id` — one URL per role:
  - translate → `YDBDOC_MODEL_TRANSLATE` (default `deepseek-v4-flash`)
  - critic → `YDBDOC_MODEL_CHECK` (default `gpt-oss-120b`)
- Request body: **only** documented OpenAI fields (`messages`, `temperature`,
  `max_tokens`). **No `model` field** — model is encoded in the path.
- Response: flat OpenAI JSON (`choices[0].message.content`), no `response` wrapper.

Implementation: `ElizaLLMClient.chat()` uses one ``requests.Session`` per client
(``session.post``) so TLS settings are explicit and reused. OAuth header only;
OpenAI SDK is not used (would inject Bearer / ``model``).

Back-compat: if only legacy ``ELIZA_BASE_URL`` is set (e.g.
`https://api.eliza.yandex.net/raw/openai/v1`), `require_eliza_api_root()` extracts
`scheme://host` as `ELIZA_API_ROOT`.

### 13.6.3. Auth — env only, OAuth only

| Rule | Detail |
|------|--------|
| **Read token from** | `ELIZA_OAUTH_TOKEN` only (`config/loader.py` → `Secrets.eliza_oauth_token`) |
| **Never via** | CLI flags, config YAML, URL query/path segments, log messages, report files |
| **Header** | `Authorization: OAuth <token>` (lowercase `authorization` in HTTP) |
| **Forbidden** | `Authorization: Bearer …` (OpenAI SDK default — not used for Eliza) |

Debug log line (safe): `Eliza request: role=… model=… url=… auth=OAuth` — token
value is **not** logged.

External schedulers (Reactor/Nirvana) must pass secrets **only** through the child
process environment, e.g.:

```python
env = dict(
    os.environ,
    YDBDOC_MODEL_PROVIDER="eliza",
    ELIZA_API_ROOT="https://api.eliza.yandex.net",
    ELIZA_OAUTH_TOKEN=token2,  # from Secret option — never argv
    YDBDOC_MODEL_TRANSLATE="deepseek-v4-flash",
    YDBDOC_MODEL_CHECK="gpt-oss-120b",
    GITHUB_TOKEN=token1,
    YDBDOC_REPO_PATH=repo_path,
)
subprocess.run(
    ["python", "-m", "ydbdoc_review", "job", "--mode", "translate", ...],
    env=env,
    check=True,
)
```

There is **no** CLI option for Eliza OAuth token (`cli.py` has no `--eliza-*` flags).

### 13.6.4. TLS / internal CA (Nirvana, Docker, local Mac)

``api.eliza.yandex.net`` is signed by Yandex internal CA — default certifi alone
is **not** enough. TLS verification must **never** be disabled (`verify=False`).

**Implementation (2026-07-14):** ``llm/tls.py`` (§6.99)

| Client | CA bundle |
|--------|-----------|
| **Eliza** ``ElizaLLMClient`` | ``eliza_tls_verify()`` = **certifi + internal PEM** (merged, cached) |
| **GitHub** ``GitHubClient`` | ``public_ca_bundle()`` = **certifi only** (ignores ``REQUESTS_CA_BUNDLE``) |
| **Yandex Cloud** OpenAI SDK | system / certifi (unchanged) |

| Env var | Behavior |
|---------|----------|
| ``YDBDOC_ELIZA_CA_BUNDLE`` | Path to internal PEM merged into Eliza bundle (preferred for local) |
| ``/etc/ssl/certs/YandexInternalCA.pem`` | Auto-used on corp Mac when file exists |
| ``REQUESTS_CA_BUNDLE`` / ``CURL_CA_BUNDLE`` | **Do not** set to internal-only PEM globally — breaks ``api.github.com`` |
| ``NODE_EXTRA_CA_CERTS`` | Ignored by Python ``requests``; use ``YDBDOC_ELIZA_CA_BUNDLE`` instead |

**Runtime requirement:** Nirvana/Reactor child env and Docker image must ship the
internal CA (mount PEM + set ``YDBDOC_ELIZA_CA_BUNDLE``).
See **08-operations** §19.4–§19.5.

**Local smoke:**

```bash
cd ydbdoc-review && source .venv/bin/activate
python -c "from dotenv import load_dotenv; load_dotenv(); from ydbdoc_review.llm.client import create_llm_client; from ydbdoc_review.config.loader import load_config; c=create_llm_client(load_config()); print(type(c).__name__, c.chat([{'role':'user','content':'OK'}], role='translate').content[:20])"
```

Requires ``YDBDOC_MODEL_PROVIDER=eliza`` and ``ELIZA_OAUTH_TOKEN`` in env.

### 13.6.5. Model ids and role chains

**Yandex Cloud** (`yandex_cloud`): ``llm.models.{role}.chain`` from YAML (unchanged).

**Eliza** — ``llm.eliza.{translate,critic}`` + env overrides; Yandex YAML slugs ignored:

| Role | Default primary | Default fallbacks (YAML) |
|------|-----------------|--------------------------|
| translate | `deepseek-v4-flash` | `gpt-oss-120b`, `gpt-oss-20b` |
| critic | `gpt-oss-120b` | `gpt-oss-20b`, `deepseek-v4-flash` |

Env (Nirvana / local):

| Env | Role |
|-----|------|
| `YDBDOC_MODEL_TRANSLATE` | translate primary |
| `YDBDOC_ELIZA_TRANSLATE_FALLBACKS` | translate fallbacks (CSV) |
| `YDBDOC_MODEL_CHECK` | critic primary |
| `YDBDOC_ELIZA_CHECK_FALLBACKS` | critic fallbacks (CSV; alias `YDBDOC_ELIZA_CRITIC_FALLBACKS`) |

Example: ``YDBDOC_ELIZA_TRANSLATE_FALLBACKS=gpt-oss-120b,gpt-oss-20b`` →
``model_chain_for_role("translate") == ["deepseek-v4-flash", "gpt-oss-120b", "gpt-oss-20b"]``
(when primary from env or YAML default).

Legacy YAML primary ``deepseek-v32`` maps to Eliza defaults above when provider is Eliza.

### 13.6.6. Retries and timeout

`ElizaLLMClient` reuses `llm.retries` and `llm.timeout_s` from config:

- Retries on: HTTP **408/5xx**, `requests.Timeout`, transient `requests.ConnectionError`
- **HTTP 429:** separate budget in `llm.retries.rate_limit` (default 6 attempts,
  5s→10s→… backoff, cap 120s) — overridable via env:
  `YDBDOC_LLM_RETRIES_RATE_LIMIT_MAX_ATTEMPTS`, `_BACKOFF_INITIAL_S`, etc.
- **429 / 5xx / unavailable:** after retries exhausted on model *i*, advance to model
  *i+1* in chain (§6.103). **429 overloaded:** one attempt per slug then advance.
- **Do not advance chain on:** HTTP **400/401/403/404**; HTTP-200 parse/format errors
  (empty choices, missing content); placeholder mismatch (translator layer).
- **Retry-After:** when present on 429 (non-overloaded), sleep that many seconds (capped by
  `rate_limit.max_backoff_s`) instead of calculated backoff
- **Fail-fast (no retry):** HTTP **400/401/403/404** and other non-retryable 4xx;
  `requests.SSLError` / cert verification errors (OAuth token never echoed in errors)
- Chain exhausted → ``LLMRetryExhaustedError`` with all model slugs listed.
- **Translator** (§6.98): separate placeholder-mismatch / rate-limit fallback at batch level.
- Finalize fail-soft skips (fence comments / text fences / prose Cyrillic) emit
  `*_translate_skipped: rate-limit — …` warnings in heuristics when LLM fails
- Backoff: `compute_backoff_s()` / `retry_delay_s()` (`llm/retry.py`)
- Warning log on retry: attempt number + model slug (no token)

Yandex Cloud path unchanged — still uses OpenAI SDK + existing retry classification.

### 13.6.7. Unified CLI entry for external schedulers

```bash
python -m ydbdoc_review job \
  --mode translate|verify \
  --repo ydb-platform/ydb \
  --pr <N> \
  --repo-path <checkout> \
  --merge-base-with origin/main
```

Same pipeline as `run` / `verify`; only invocation surface differs. Tag **`v0.2.0`**
for Reactor/Nirvana during provider migration; **`v0.1.0`** stays on bugfix line for
`ydb` GitHub Actions (§01-overview).

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

[← Memory Bank index](../../MEMORY_BANK.md)
