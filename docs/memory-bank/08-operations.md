# Memory Bank — Operations

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

---

## 19. Logging

### 19.1. Library

`rich.logging.RichHandler` for human-friendly local output; plain stdout for
CI (Actions captures stdout fine).

### 19.2. Levels

- **INFO**: high-level progress (per file, per phase).
- **DEBUG**: per-batch, per-segment events; LLM request/response trimmed.
- **WARNING**: retry attempts, heuristic flags, fallback model used.
- **ERROR**: unrecoverable per-file failures (still don't fail the job).

### 19.4. Docker image (GitHub Action)

`action.yml` is a **composite** action (`action-docker.sh`):

1. **Try** `docker build` from `Dockerfile` in the checked-out action ref (normal
   path after `git tag -f v0.1.0` — no GHCR publish wait).
2. **On build failure** (e.g. Docker Hub / ECR timeout) → `docker pull`
   `ghcr.io/ydb-platform/ydbdoc-review:<GITHUB_ACTION_REF>` and run that image.

Base image: `public.ecr.aws/docker/library/python:3.12-slim` (Docker Hub mirror).

**GHCR publish (optional):** workflow `.github/workflows/docker-publish.yml` —
`workflow_dispatch` only. Run manually when you want a fresh fallback image after
large changes; not required for every tag move.

After bugfixes:

```bash
git tag -f v0.1.0 HEAD && git push -f origin v0.1.0
# re-add doc_translate in ydb — no wait for GHCR
```

`YDBDOC_GIT_SHA` is set at local build time from the action ref; GHCR fallback bakes
SHA at last manual publish.

---

## 20. Cost tracking

### 20.1. Per-call tracking

Every `llm.chat()` call records via `UsageTracker`:
- `model_slug`, `input_tokens`, `output_tokens`, `latency_ms`, `retries`,
  `success`, optional `role` (`translate` | `critic` | `analyze`).

Translate/repair pass `role="translate"` even when `model=` is explicit so
per-role breakdown appears in reports (§6.38).

### 20.2. Aggregation per PR

- **Session total:** `usage_tracker` on the shared `YandexLLMClient` for the run.
- **Per file:** `metrics_since(record_start)` — delta since file pipeline start
  (avoids cumulative double-count in `FileTranslationResult`).

### 20.3. Price table (manual)

`MODEL_PRICE_RUB_PER_1K` in `llm/usage.py` — **₽ per 1000 tokens** (input, output),
sync mode incl. VAT. Yandex AI Studio does not return prices in API responses.
Update when tariffs change (see [Yandex AI Studio pricing](https://yandex.cloud/ru/docs/foundation-models/pricing)
and community summaries e.g. [Habr](https://habr.com/ru/articles/1030524/)).

| Model slug (examples) | In ₽/1K | Out ₽/1K |
|-----------------------|---------|----------|
| `yandexgpt-5-lite` | 0.20 | 0.20 |
| `yandexgpt-5.1` | 0.80 | 0.80 |
| `deepseek-v32` | 0.50 | 0.40 |
| `deepseek-v4-flash` | 0.30 | 0.50 |

Formula: `(input_tokens / 1000) × in_price + (output_tokens / 1000) × out_price`.

### 20.4. Reporting

- **Source PR** (`build_source_pr_comment`): table row `Стоимость | ~₽X.XX`.
- **Translation PR** (`build_full_report`): section «Стоимость и токены» with
  per-role tokens, total, models — including 🟢 all-green reports (§6.38).
- Toggle: `reporting.include_cost`, `reporting.include_token_usage` in config.

### 20.5. Backlog: persistent cost log

`docs-internal/cost-log.md` (in `ydbdoc-review` repo) maintained by a script
that appends one line per PR run. Not in MVP.

---

---

## 21. Glossary of terms used in this Memory Bank

- **AST / IR**: our pydantic representation of a parsed markdown document.
- **Segment**: a translatable unit extracted from AST (a paragraph, a heading,
  a table cell, a list item, etc.).
- **Placeholder / marker**: `⟦C1⟧`, `⟦U1⟧`, etc., representing a protected
  inline atom in the LLM-visible text.
- **Batch**: a group of segments sent to the LLM in one request.
- **Round-trip**: parse → render → equal (or idempotent after first pass).
- **Identity**: extract → re-insert with no changes → equal to direct render.
- **Translation PR**: the PR created by `doc_translate` against the source
  PR's HEAD branch.
- **Source PR**: the PR in `ydb-platform/ydb` that the user labeled.
- **Verify**: re-running QA on a translation PR via `doc_verify` label.
- **YFM**: Yandex Flavored Markdown — the markdown superset Diplodoc parses.
- **Diplodoc**: open-source documentation framework by Yandex.
- **Langlink**: Wikipedia interlanguage link between article titles in different
  editions; resolved via MediaWiki API (§6.37).

---

**End of Memory Bank.**

---

[← Memory Bank index](../../MEMORY_BANK.md)
