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

As of 2026-07-14, **`v0.1.0` → `203956a`** — §22 planner + step-3 scope fix,
harness import, Eliza hardening, glossary MD037 postprocess, report UX (§6.96),
text-fence JSON (§6.97). WIP (not yet tagged): §6.98–§6.100 (429 fallback, TLS split, shutdown).
First rollout incident and re-run playbook: **09-navigation-scope** §22.8, §22.10–§22.11.

ydb workflow checks out the action at `@v0.1.0` (or `@v0.2.0` for schedulers);
the runner builds a fresh image from that tag's `Dockerfile`.

**External scheduler loop (Reactor/Nirvana, Eliza — tag `v0.2.0`):**

```bash
git tag -f v0.2.0 HEAD && git push -f origin v0.2.0
```

Parent reaction builds `env` for the child — **secrets only via env**, not CLI.
**TLS:** mount internal CA PEM in the container and set `YDBDOC_ELIZA_CA_BUNDLE`
(merged with certifi in code — §6.99); without it Eliza calls fail with
`SSLError` (see **06-llm-config** §13.6.4).

| Env var | Purpose |
|---------|---------|
| `YDBDOC_MODEL_PROVIDER=eliza` | Switch LLM transport |
| `ELIZA_API_ROOT` | API host (default `https://api.eliza.yandex.net`) |
| `ELIZA_OAUTH_TOKEN` | OAuth token (Secret option → child env only) |
| `YDBDOC_MODEL_TRANSLATE` | Internal model id for translate |
| `YDBDOC_MODEL_CHECK` | Internal model id for critic |
| `YDBDOC_ELIZA_CA_BUNDLE` | Internal PEM **merged with certifi** for Eliza only (§6.99) — **not** `REQUESTS_CA_BUNDLE` globally |
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
| `doc_verify` `orphan_toc_page` | Translated EN page not linked from any EN toc (§6.117) | Parent toc missing `include`/`href` (§6.116); re-run `doc_translate` after tag bump, or fix parent toc on the translation PR |
| `build-docs` ENOENT on child toc | Child sidebar not merged | Pre-§22: §6.84 supplement. §22+: check planner BFS on `include.path`; tag bump when ready |
| `doc_verify` `empty_toc` on new EN sidebar | Absent EN file + empty scoped merge (§6.85) | Tag bump + re-run `doc_translate` |
| `build-docs` `YFM003 unreachable-link` in **glossary** | Hub links to pages outside EN toc (variant A §6.107) | Auto-strip on translate; restore links when target page is translated + added to toc |
| `build-docs` `YFM003 unreachable-link` elsewhere | Page linked but not in toc (§6.86) | Indented `href:` parse + re-run `doc_translate` |
| `python:3.12-slim` / ECR timeout on build | Registry unreachable from runner | Retry; or run `docker-publish` and rely on GHCR fallback |
| Action exits 0 but no report on translation PR | Fixed in §6.48 — update `@v0.1.0` | Tag must include `_safe_post_issue_comment` + report-first order |
| CI red after translate OK; log `AttributeError: file_url` | §6.101 regression in report builder | Tag bump; re-run **`doc_verify`** label on translation PR (no re-translate) |
| `trigger-translation-ci` skipped | `ydbdoc-review` job failed (exit 1) | Same as above; see **07-pipeline** §16.7 |
| GHCR pull 404 on fallback | Image never published for that ref | Run `docker-publish` workflow_dispatch for current tag |
| Eliza `SSLError` / cert verify failed | Internal CA not in Eliza merged bundle | Set `YDBDOC_ELIZA_CA_BUNDLE=/etc/ssl/certs/YandexInternalCA.pem` in `.env` (§6.99). Do **not** use `REQUESTS_CA_BUNDLE` globally |
| GitHub `SSLError` on `api.github.com` after CA change | `REQUESTS_CA_BUNDLE` set to internal-only PEM | Remove from `~/.zshrc`; GitHub client uses certifi explicitly (§6.99) |
| Eliza `HTTP 429 overloaded` | Model pool saturated | §6.103 chain: defaults or `YDBDOC_ELIZA_*_FALLBACKS`; `YDBDOC_LLM_CONCURRENCY_BATCHES_PER_FILE=1` |
| Local job used Yandex Cloud not Eliza | Agent/non-login shell skipped `~/.zshrc`; `.env` had no `YDBDOC_MODEL_PROVIDER=eliza` | `YDBDOC_MODEL_PROVIDER=eliza` in `.env` or `zsh -lic '…'`; tokens in `~/.zshrc` |
| `Ctrl+C` / `pkill` does not stop `job` | Thread pool + long 429 sleep in workers | §6.100; `pkill -9 -f ydbdoc_review`; close terminal tab |
| Translation PR **не создан** on source PR; ``docs/en/_includes/go/…`` in gaps | Mis-resolved shared ``docs/_includes/`` snippet (§6.80.5, #43997) | Tag with ``include_paths`` fix; re-run ``doc_translate`` |
| Translation PR exists but 🔴 on ``glossary.md`` placeholder | ``doc_verify`` critic ``atom_map`` noise on multi-link terms (#46435/#46431) | Tag with ``placeholder_align`` U-slot fix; or merge after manual glossary pass |
| «Автоперевод не работает» | Often push blocked (completeness) or 🔴 QA, not missing job | Check source PR comment: «translation PR не создан» vs translation PR # with report |

`action_release_label()` in reports: `GITHUB_ACTION_REF` + `YDBDOC_GIT_SHA` from
image build (local) or last GHCR publish (fallback).

### 19.5. Local Eliza dry-run (`job --mode translate`)

**Two providers — do not conflate:**

| Where | LLM | Config source |
|-------|-----|----------------|
| **ydb GitHub Actions** `doc_translate` | Yandex Cloud FM | `YANDEX_CLOUD_*` secrets; default provider |
| **Local / Reactor / Nirvana** | Eliza internal | `YDBDOC_MODEL_PROVIDER=eliza`, `ELIZA_OAUTH_TOKEN` |

**Typical local setup:**

- `~/.zshrc`: `ELIZA_OAUTH_TOKEN`, `YDBDOC_MODEL_PROVIDER=eliza`, `ELIZA_API_ROOT`,
  `YDBDOC_MODEL_TRANSLATE=deepseek-v4-flash`, `YDBDOC_MODEL_CHECK=gpt-oss-120b`,
  `GITHUB_TOKEN` (via `YDB_GH_TOKEN`).
- `ydbdoc-review/.env`: `YDBDOC_MODEL_PROVIDER=eliza`,
  `YDBDOC_ELIZA_CA_BUNDLE=/etc/ssl/certs/YandexInternalCA.pem`,
  `YDBDOC_ELIZA_TRANSLATE_FALLBACKS=gpt-oss-120b,gpt-oss-20b`,
  `YDBDOC_ELIZA_CHECK_FALLBACKS=gpt-oss-20b`,
  `YDBDOC_LLM_CONCURRENCY_BATCHES_PER_FILE=1`.
- **Do not** set `REQUESTS_CA_BUNDLE` to internal CA in zshrc (§6.99).

**Checkout source PR in ydb clone:**

```bash
cd /path/to/ydb
git fetch origin pull/{N}/head:pr-{N} && git checkout pr-{N}
git fetch origin main
```

**Dry-run (no commit/push/comments):**

```bash
cd ydbdoc-review && source .venv/bin/activate
python -m ydbdoc_review job \
  --mode translate \
  --repo ydb-platform/ydb \
  --pr {N} \
  --repo-path /path/to/ydb \
  --merge-base-with origin/main \
  --dry-run
```

**Agent / non-interactive shells** must load zshrc tokens:

```bash
zsh -lic 'cd ydbdoc-review && source .venv/bin/activate && python -m ydbdoc_review job ...'
```

**§22 validation PRs (2026-07-14):** one at a time — [#44457](https://github.com/ydb-platform/ydb/pull/44457)
(re-trigger CI after tag bump), [#43010](https://github.com/ydb-platform/ydb/pull/43010)
(local Eliza dry-run, scope ~13 doc paths), [#43997](https://github.com/ydb-platform/ydb/pull/43997) next.

**Known manual QA on #44457 translation:** Wikipedia links in `execution_process.md`
(DML/DDL ru-slug on `en.wikipedia.org`) — MediaWiki langlinks API cannot auto-fix;
report shows line link (§6.96).

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
