# Memory Bank — Development guide

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

---

## 7. Test strategy

### 7.1. Layout

```
tests/
├── unit/                                  fast, no I/O, no LLM
│   ├── test_parser_round_trip.py          plain markdown
│   ├── test_yfm_variables.py
│   ├── test_yfm_notes.py
│   ├── test_yfm_tabs.py
│   ├── test_yfm_includes.py
│   ├── test_yfm_conditionals.py
│   ├── test_yfm_cuts.py
│   ├── test_yfm_terms.py
│   ├── test_yfm_image_size.py
│   ├── test_segmentation.py
│   ├── test_reinsert.py
│   ├── test_chunker.py
│   ├── test_config.py               YAML load, env overrides, secrets
│   ├── test_llm_structured.py       JSON/fence parsing
│   ├── test_llm_retry.py            backoff + error classification
│   ├── test_llm_client.py           mocked YandexLLMClient
│   ├── test_llm_usage.py            UsageTracker + cost estimate
│   ├── test_reinsert_coverage.py    reinsert error paths + segment kinds
│   ├── test_renderer_coverage.py    renderer edge cases
│   └── test_glossary.py             glossary loader + prompt YAML
│   ├── test_translator.py           segment translator (mocked LLM)
│   └── test_critic.py               critic parse/apply/review (mocked LLM)
├── integration/                           on real fixtures, may include LLM
│   ├── test_real_files_round_trip.py      parametrized over 66 fixtures
│   └── test_llm_smoke.py                  live API (local only, @pytest.mark.llm)
└── fixtures/markdown_files/               real .md from ydb-platform/ydb
    ├── ru/...
    └── en/...
```

Future:
- `tests/integration/test_end_to_end.py` — full pipeline on a real file pair.

### 7.2. Counters (post D.4)

- **Default CI/local run**: unit + fixture integration (no LLM smoke).
- **Integration (LLM smoke)**: 3 tests in `test_llm_smoke.py`, **local only** —
  not in default `pytest` run (see §7.3).
- **Coverage (overall package)**: 90%+ target; run with `--cov=ydbdoc_review`.

### 7.2.1. Coverage policy (90% target)

**Goal: 90%+ line coverage** for core pipeline packages:

| Package | Target | Notes (end of Phase C) |
|---|---|---|
| `parsing/` | 90%+ | ✅ ~91–100% per module |
| `segmentation/` | 90%+ | ✅ ~92–100% (`reinsert.py` covered via `test_reinsert_coverage.py`) |
| `rendering/` | 90%+ | ✅ ~95% (`test_renderer_coverage.py`) |
| `config/` | 90%+ | ✅ ~95% |
| `llm/` | 90%+ | ✅ unit tests mocked; live smoke optional |
| `translation/` | 90%+ | ✅ translator + critic (mocked LLM) |
| `validation/` | 90%+ | ✅ markers + cli_tokens wired in translator |
| `github/`, `pipeline/` | lower until implemented | integration when added |

### 7.2.2. LLM integration tests (policy)

**Yes, we write them — but they are opt-in, not CI gates.**

| Layer | What | Where | When to run |
|---|---|---|---|
| **Unit** | Mocked `YandexLLMClient`; parse/validate/apply logic | `tests/unit/test_*` | Every commit, CI |
| **Fixture integration** | Parser/segmentation round-trip on real `.md` | `test_real_files_round_trip.py` | Every commit, CI |
| **LLM smoke** | 1–3 live API calls (translate JSON, critic JSON) | `test_llm_smoke.py`, `@pytest.mark.llm` | Local only, credentials required |
| **End-to-end** | Full `translate_file` on fixture pair | `test_end_to_end.py` (Phase F) | Local / nightly, not MVP CI |

Rules:
- Default `pytest` **excludes** `test_llm_smoke.py` (`pyproject.toml` addopts).
- Smoke tests skip automatically when `YDBDOC_YC_*` env vars are missing.
- **Do not** fail CI on LLM tests — API keys, quota, and network are not guaranteed in Actions.
- New LLM-facing code: **unit tests with mocks first** (90% coverage); add smoke only
  when a new role or JSON schema needs a one-shot live sanity check.

Invoke locally:

```bash
pytest tests/integration/test_llm_smoke.py -m llm -v
```

```bash
pytest tests/unit/ tests/integration/test_real_files_round_trip.py \
  --cov=ydbdoc_review --cov-report=term-missing
```

Do **not** fail CI on LLM smoke tests — they require credentials and network.

### 7.3. How to run

```bash
pytest                                    # unit + fixture integration (no LLM smoke)
pytest tests/unit/ -v                     # unit only
pytest tests/integration/ -v --tb=line    # fixture integration only
pytest tests/integration/test_llm_smoke.py -m llm -v   # live API (needs .env)
pytest -k "tabs"                          # by keyword
pytest -m "not slow"                      # exclude slow markers
pytest --cov=ydbdoc_review --cov-report=term-missing   # coverage report
```

Default `pytest` **ignores** `test_llm_smoke.py` (see `pyproject.toml` addopts).
LLM smoke tests are marked `@pytest.mark.llm` and only run when invoked explicitly
(requires `YDBDOC_YC_*` or v1 alias env vars + network).

### 7.4. Fixture refresh

```bash
./scripts/fetch_fixtures.sh
python scripts/scan_yfm.py    # YFM-construct frequency report
```

Fixtures are committed and not auto-updated, so older versions stay reproducible.

---

---

## 9. TODO / Backlog (not in main roadmap)

- **Front matter translation** (Phase B.4 above).
- **Glossary YAML maintenance**: now seeded with ~30-50 terms manually.
  Future: script that parses https://ydb.tech/docs/ru/concepts/glossary into
  YAML and proposes a diff. Currently `prompts/glossary.yaml` is the source
  of truth, maintained by hand. **Priority: low (10th).**
- **Relevant-subset glossary injection**: currently we inject the full
  glossary into every prompt. Optimization: detect which terms appear in
  the batch text and only include matching entries. Saves tokens.
- **Strikethrough rendering**: GFM strikethrough tokens are dropped silently.
  Add `InlineStrike` node. Low priority — verify if YDB uses it.
- **Hard line breaks**: rendered as `␠␠\n`; some authors prefer `\\`.
- **Indented code blocks**: rendered with 4-space indent. Check YDB usage.
- **Image `{ width="100" }` form** (alternative Diplodoc): not modelled.
- **Delimited fallback** for translator: if JSON parsing fails 3x, fall back
  to `<<<S0001>>>...<<<END>>>` format. Not in MVP.
- **Override config in `ydb` repo**: allow `ydb/docs/.ydbdoc-review.yaml`
  to override per-repo settings. Not in MVP.
- **Cost dashboard**: collect cost from each PR run, persist to a markdown
  log. Currently just reported per-PR.

---

---

## 10. Working agreements (AI assistant ↔ human)

- **One step at a time.** Each step produces something testable.
- **Tests are mandatory.** No "works on my machine".
- **Round-trip on real files is the canonical signal** — must stay green forever.
- **Memory Bank is updated after every meaningful step**, or at phase boundaries.
- **Code delivered as copy-pasteable blocks**; the human applies, runs, reports.
- **The AI assistant does NOT have GitHub write access.** The human commits and pushes.
- **Failed test output is shared in full** — never paraphrased. The AI needs
  to see actual diffs, tracebacks, and tool output to debug correctly.
- **Memory Bank verbosity is intentional** — a different AI may take over.

---

---

## 11. Environment

### 11.1. Local development

- macOS, Python 3.14 (or 3.11+).
- venv in `.venv/`, dependencies pinned in `pyproject.toml`.
- `.env` (gitignored) with credentials.
- `pytest` for tests.
- Editor of choice; suggest ruff + mypy.

### 11.2. CI

- GitHub Actions in `ydb-platform/ydb` repo, two workflows:
  - `ydbdoc-review (doc_translate label)` → calls `ydb-platform/ydbdoc-review@v0.1.0` with `mode: run` (default).
  - `ydbdoc-review (doc_verify label)` → same action with `mode: verify`.
- Action is a Dockerfile-based action; the container runs Python 3.11+.
- Secrets in the `ydb` repo:
  - `YANDEX_CLOUD_FOLDER_DOC_REVIEW`
  - `YANDEX_CLOUD_API_KEY_DOC_REVIEW`
  - `YDBDOC_PUSH_PAT` (for push to head of PR; required for forks)
- Tag `v0.1.0` will be **moved forward** to the v2 merge commit at release time
  (the user has limited ability to change CI config in `ydb`).

### 11.3. Tooling

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Optional:

```bash
ruff check src/ tests/
ruff format src/ tests/
mypy src/ydbdoc_review/
```

---

---

[← Memory Bank index](../../MEMORY_BANK.md)
