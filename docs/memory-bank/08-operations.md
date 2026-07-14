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

### 19.4. GitHub Action runtime (Docker)

**Files (repo root):**

| File | Purpose |
|------|---------|
| `action.yml` | Composite action metadata + `INPUT_*` env for `action-docker.sh` |
| `action-docker.sh` | Build or pull image, `docker run` with workspace mount |
| `Dockerfile` | Python 3.12 + git + `pip install` package; `ENTRYPOINT` → `entrypoint.sh` |
| `entrypoint.sh` | Map `YDBDOC_REPO_PATH` / `GITHUB_WORKSPACE`; invoke `ydbdoc-review run\|verify` |
| `.github/workflows/docker-publish.yml` | Optional push to GHCR (`workflow_dispatch`) |

**Runtime flow (`action-docker.sh`):**

1. `docker build -t ydbdoc-review-local:$$ -f "$ACTION_PATH/Dockerfile" "$ACTION_PATH"`
   with `YDBDOC_GIT_SHA` build-arg (defaults to action ref).
2. On failure → log stderr, `docker pull ghcr.io/ydb-platform/ydbdoc-review:<ref>`
   where `<ref>` = `GITHUB_ACTION_REF` without `refs/tags/` (e.g. `v0.1.0`).
3. `docker run --rm -v "$GITHUB_WORKSPACE:/github/workspace"` + forwarded env
   (`GITHUB_TOKEN`, `YDBDOC_*`, `YANDEX_CLOUD_*`, `INPUT_*`, …).
4. Remove local build tag on exit.

Base image in `Dockerfile`: `public.ecr.aws/docker/library/python:3.12-slim`
(AWS ECR Public mirror of Docker Hub `library/python`).

**Why not native `image: Dockerfile`?** GitHub builds that internally with no
fallback hook when registry pulls fail.

**GHCR fallback image:**

- Registry: `ghcr.io/ydb-platform/ydbdoc-review`
- Tags: `<ref>` (e.g. `v0.1.0`) and `<git-sha>` on manual publish
- Publish: Actions → **Publish action image** → Run workflow (not on every tag push)
- Stale fallback is acceptable for emergency use; run publish after major releases
  or if builds keep failing on runners

**Typical bugfix loop (no GHCR):**

```bash
# ydbdoc-review repo — move tag when ready to ship to ydb CI
git tag -f v0.1.0 HEAD && git push -f origin v0.1.0
# ydb: delete ydbdoc-review/pr-{N} branch, re-add doc_translate on source PR
```

As of 2026-07-14, §22 planner is on `main` (`d68812f`) but tags were **not** moved
yet — ydb still runs pre-§22 until the tag bump. See **09-navigation-scope** §22.8.

ydb workflow checks out the action at `@v0.1.0` (or `@v0.2.0` for schedulers);
the runner builds a fresh image from that tag's `Dockerfile`.

**External scheduler loop (Reactor/Nirvana, Eliza — tag `v0.2.0`):**

```bash
git tag -f v0.2.0 HEAD && git push -f origin v0.2.0
```

Parent reaction builds `env` for the child — **secrets only via env**, not CLI.
**TLS:** mount internal CA PEM in the container and set `YDBDOC_ELIZA_CA_BUNDLE`
(or `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE`); without it Eliza calls fail with
`SSLError` (see **06-llm-config** §13.6.4).

| Env var | Purpose |
|---------|---------|
| `YDBDOC_MODEL_PROVIDER=eliza` | Switch LLM transport |
| `ELIZA_API_ROOT` | API host (default `https://api.eliza.yandex.net`) |
| `ELIZA_OAUTH_TOKEN` | OAuth token (Secret option → child env only) |
| `YDBDOC_MODEL_TRANSLATE` | Internal model id for translate |
| `YDBDOC_MODEL_CHECK` | Internal model id for critic |
| `YDBDOC_ELIZA_CA_BUNDLE` | PEM path for internal CA (Eliza TLS); or set `REQUESTS_CA_BUNDLE` |
| `GITHUB_TOKEN` | GitHub API for PR/branch/comments |
| `YDBDOC_REPO_PATH` | Checkout path inside container |

Smoke curl (local, token from env):

```bash
curl -s "$ELIZA_API_ROOT/raw/internal/deepseek-v4-flash/v1/chat/completions" \
  -H "authorization: OAuth $ELIZA_OAUTH_TOKEN" \
  -H "content-type: application/json" \
  -d '{"messages":[{"role":"user","content":"Translate to English: Привет!"}]}'
```

See **06-llm-config** §13.6 for full contract.

**Troubleshooting:**

| Symptom | Likely cause | Mitigation |
|---------|----------------|------------|
| `doc_verify` `missing_toc_target` | EN toc lists page outside translate scope | Pre-§22: tag bump + re-run `doc_translate`. §22+: planner should queue page in step 3 — if still failing, check `nav_cases/` fixture |
| `build-docs` ENOENT on child toc | Child sidebar not merged | Pre-§22: §6.84 supplement. §22+: check planner BFS on `include.path`; tag bump when ready |
| `doc_verify` `empty_toc` on new EN sidebar | Absent EN file + empty scoped merge (§6.85) | Tag bump + re-run `doc_translate` |
| `build-docs` `YFM003 unreachable-link` | Page linked but not in toc (§6.86) | Indented `href:` parse + re-run `doc_translate` |
| `python:3.12-slim` / ECR timeout on build | Registry unreachable from runner | Retry; or run `docker-publish` and rely on GHCR fallback |
| Action exits 0 but no report on translation PR | Fixed in §6.48 — update `@v0.1.0` | Tag must include `_safe_post_issue_comment` + report-first order |
| `trigger-translation-ci` skipped | `ydbdoc-review` job failed (exit 1) | Same as above; see **07-pipeline** §16.7 |
| GHCR pull 404 on fallback | Image never published for that ref | Run `docker-publish` workflow_dispatch for current tag |
| Eliza `SSLError` / cert verify failed | Internal CA not in runtime | Mount PEM; set `YDBDOC_ELIZA_CA_BUNDLE` or `REQUESTS_CA_BUNDLE` (§13.6.4) |
| Translation PR **не создан** on source PR; ``docs/en/_includes/go/…`` in gaps | Mis-resolved shared ``docs/_includes/`` snippet (§6.80.5, #43997) | Tag with ``include_paths`` fix; re-run ``doc_translate`` |
| Translation PR exists but 🔴 on ``glossary.md`` placeholder | ``doc_verify`` critic ``atom_map`` noise on multi-link terms (#46435/#46431) | Tag with ``placeholder_align`` U-slot fix; or merge after manual glossary pass |
| «Автоперевод не работает» | Often push blocked (completeness) or 🔴 QA, not missing job | Check source PR comment: «translation PR не создан» vs translation PR # with report |

`action_release_label()` in reports: `GITHUB_ACTION_REF` + `YDBDOC_GIT_SHA` from
image build (local) or last GHCR publish (fallback).

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
