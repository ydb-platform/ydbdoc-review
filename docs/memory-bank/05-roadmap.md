# Memory Bank — Roadmap

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

---

## 8. Roadmap

### Phase A — Parser/renderer foundation ✅ COMPLETE
- [x] 2.1 markdown parser + renderer + round-trip on synthetic markdown
- [x] 2.2 YFM `{{ variable }}` plugin
- [x] 2.3 YFM `{% note %}` plugin
- [x] 2.4 YFM `{% list tabs %}` plugin + table pipe-escape fix
- [x] 2.5 YFM `{% include %}` plugin
- [x] 2.6 YFM `{% if %}…{% endif %}` plugin
- [x] 2.7 YFM `{% cut %}` plugin
- [x] 2.8 Term definitions `[*term]: definition`
- [x] 2.9 Image with size attribute `![alt](src =100x100)`
- [x] 2.10 Variables inside link URLs `[text]({{ var }})`

### Phase B — Segmentation ✅ COMPLETE
- [x] B.1 Segment extractor + inline protector
- [x] B.2 Re-insertion + identity round-trip on all 33 real fixtures
- [x] B.3 Chunker (greedy character budget, no segment split)

### Phase B.4 — Front matter ✅ COMPLETE
- [x] `parsing/front_matter.py` — parse/dump YAML; `title` / `description` translatable
- [x] `segmentation/extractor.py` — front matter segments (`SegmentKind.FRONT_MATTER`)
- [x] `segmentation/reinsert.py` — apply translations; pass through `vcsPath`, `editable`, …
- [x] Unit tests: `test_front_matter.py`

### Phase C — LLM client ✅ COMPLETE
- [x] OpenAI-compatible client for Yandex AI Studio
  - Endpoint: `https://ai.api.cloud.yandex.net/v1`
  - Auth: `Api-Key <key>` via openai SDK
  - Model URI: `gpt://<folder_id>/<model_slug>`
- [x] Config loader: YAML default + env override (Pydantic schema, greedy path
      resolution, unknown overrides ignored — see §13.3)
- [x] Retry with exponential backoff
- [x] Model fallback chain on `Failed to get model`
- [x] JSON output parsing with code-fence stripping (`structured.py`)
- [x] Pydantic schema validation (`parse_json_model`)
- [x] Usage tracking (input/output tokens, latency, retries)
- [x] Smoke integration test (local only, `@pytest.mark.llm`)

Public API: `YandexLLMClient.from_config(cfg).chat(messages, role=...)`.
See `ydbdoc_review.llm` package.

### Phase D — Translator + Critic ✅ COMPLETE

#### D.1 — Glossary ✅ COMPLETE
- [x] `prompts/glossary.yaml` — seed (~25 entries)
- [x] `translation/glossary.py` — load default/custom path, `to_prompt_yaml()`
- [x] Unit tests: `tests/unit/test_glossary.py`

#### D.1.5 — Navigation YAML (TOC + redirects) ✅ COMPLETE
- [x] `navigation/toc.py` — parse, `toc_translate_scope`, `merge_en_toc_yaml`, `validate_toc_merge`
- [x] `navigation/redirects.py` — same pattern for Diplodoc redirect lists
- [x] Unit tests: `tests/unit/test_navigation_toc.py`, `test_navigation_redirects.py`
- [x] `navigation/paths.py` — toc/redirect path detection (Phase E)

#### D.2 — Prompt templates ✅ COMPLETE
- [x] `prompts/v1/system_common.md`, `translate.md`, `critic.md`, `verify.md`, `analyze.md`, `en_style_guide.md`
- [x] `translation/prompts.py` — load/render templates, build chat messages + batch JSON
- [x] Unit tests: `tests/unit/test_prompts.py`

#### D.3 — Translator ✅ COMPLETE
- [x] `translation/schemas.py` — pydantic JSON I/O models
- [x] `translation/translator.py` — per-batch translate, per-segment fallback, cache, parallel batches
- [x] `validation/markers.py`, `validation/cli_tokens.py` — structural checks
- [x] Unit tests: `test_translator.py`, `test_validation_markers.py`

#### D.4 — Critic ✅ COMPLETE
- [x] `translation/schemas.py` — `CriticResponse`, `CriticIssueOut`
- [x] `translation/critic.py` — batched `run_critic`, `run_verify`, merge + apply fixes
- [x] Unit tests: `tests/unit/test_critic.py`
- [x] LLM smoke: `test_smoke_critic_json` in `test_llm_smoke.py` (local only)

#### D.5 — Per-file pipeline ✅ COMPLETE
- [x] `pipeline/types.py` — `FileTranslationResult`, verdict + usage summary
- [x] `pipeline/translate_file.py` — parse → translate → reinsert → critic → verify → heuristics → render
- [x] Unit tests: `tests/unit/test_translate_file.py` (incl. heuristic verdict bump)

### Phase E — Validation heuristics ✅ COMPLETE
- [x] Placeholder count check (`validation/markers.py` — wired in translator)
- [x] CLI-token preservation (`validation/cli_tokens.py` — wired in translator)
- [x] `validation/heuristics.py` — length ratio, cyrillic-in-EN, fence/heading/list-tab parity
- [x] `validation/fence_comments.py` — translate Cyrillic ``//``/``#``/``--`` fence comments (line-start + trailing ``//``); `cyrillic_in_fence` QA (§6.39, §6.46, §6.81)
- [x] `validation/prose_cyrillic.py` — translate residual Cyrillic in EN prose/backticks (§6.45)
- [x] `pipeline/pairs.py` — locale ``ru|en/…/_includes/*.md`` in doc_translate scope (§6.41)
- [x] TOC / redirect merge validation wrappers (`validate_toc_merge`, `validate_redirect_merge`)
- [x] `navigation/paths.py` — detect toc/redirect YAML paths
- [x] Wired in `translate_file` (markdown heuristics + verdict bump)
- [x] Unit tests: `test_validation_heuristics`, `test_navigation_paths` (list_tab, redirect nav, translate_file integration)

### Phase F — Pipeline & orchestrator ✅ COMPLETE
- [x] `pipeline/pairs.py` — RU/EN mirroring, `build_doc_pairs`
- [x] `pipeline/analyze.py` — deterministic full re-translate plans (§6.30); LLM analyze deprecated for CI
- [x] `pipeline/orchestrator.py` — `run_pr_translation` (sequential, per-PR cache)
- [x] `translate_file` — `enable_translate=False` for critic-only QA
- [x] Unit tests: `test_pipeline_pairs`, `test_pipeline_analyze`, `test_pipeline_orchestrator`
- [x] `navigation/paths.py` — used by navigation merge validation (Phase E)
- [x] `pipeline/navigation_merge.py` + `completeness.py` wired in `workflow.py` (§6.17, §6.32)
- [x] `navigation/scope_planner.py` — unified TOC scope (§22; supersedes supplement modules)
- [x] `validation/toc_targets.py` — EN toc link target existence (§6.83–§6.84)
- [x] Absent-EN toc full mirror + include-only parse (§6.85)

### Phase G — GitHub integration ✅ COMPLETE
- [x] `github/client.py` — REST API (PR, files, comments, open PR)
- [x] `github/git_ops.py` — local git diff, branch, commit, push
- [x] `github/pr.py` — enumerate changes, load pair contents, PR context; fork detection + upstream push helpers
- [x] `github/workflow.py` — `run_doc_translate`, `run_doc_verify`
- [x] `Secrets.require_github()` in config loader
- [x] Unit tests: `test_github_client`, `test_github_git_ops`, `test_github_pr`, `test_github_workflow`, `test_reporting_builder`
- [x] Navigation YAML scoped merge in `workflow.py` (§6.17; §22 planner; mirror §6.85)

Public API: `run_doc_translate`, `run_doc_verify` from `ydbdoc_review.github`.

### Phase H — Reporting ✅ COMPLETE
- [x] Per-file verdict table + detailed critic/heuristic sections (§17.2)
- [x] Heuristics block separate from critic issues
- [x] Cost / usage block with translator/critic token split via `UsageTracker`
- [x] Retry stats, models by role, prompt version footer
- [x] Source PR comment: new vs updated file counts
- [x] Glossary `<details>` block in full report
- [x] `LLMUsage.role` + `UsageTracker.tokens_for_role()` for role-aware reporting
- [x] Workflow passes `usage` + `glossary` into report builders
- [x] Unit tests: expanded `test_reporting_builder`, `test_llm_usage`

### Phase I — Glue & shipping
- [x] CLI (`run`, `verify`, `list-models`, `translate-file`, `extract`) — `cli.py`
- [x] Docker `entrypoint.sh` + `Dockerfile` (pip install package)
- [x] Move tag `v0.1.0` to `doc-translate-ng` HEAD (`24589ad`) for real PR testing
- [x] Rewrite README
- [x] `ARCHITECTURE.md` and `CONTRIBUTING.md`
- [x] Example workflows updated for v2 env vars
- [x] Unit tests: `test_cli.py` (run, verify, translate-file, extract, list-models)

### Phase J — Unified navigation scope (TOC redesign) ✅ DONE

See **09-navigation-scope** §22.

- [x] **J.1** Design doc + roadmap (§22)
- [x] **J.2** `navigation/scope_planner.py` — `plan_translation_scope`
- [x] **J.3** Real PR fixtures (`scripts/fetch_nav_fixtures.py`, `tests/fixtures/nav_cases/`)
- [x] **J.4** Golden tests: `test_nav_scope_planner.py` (cases #45181, #44820, #43530)
- [x] **J.5** Wire planner into `workflow.py` (replace supplement chain)
- [x] **J.6** Merge layer reads `TranslationScopePlan`; drop `extra_toc_hrefs` axis
- [x] **J.7** `doc_verify` uses same planner; delete legacy supplement modules

### Phase K — ACL, YDB quota, S3 transcripts, continue (§6.134) 🔲 IN PROGRESS

YDB connection locked (§20.7–§20.8 DDL applied). S3 bucket locked (§20.10);
cloud size quota may block writes until raised.

- [x] **K.0** Design locked (§6.134) + YDB/S3 env contract (§20.6–§20.10)
- [x] **K.1 ACL** — `ops/gates.py` allowlist from `YDBDOC_ALLOWED_ACTORS`;
      wire translate/verify/continue; deny comment; tests `test_ops_gates.py`;
      update `examples/*.yml`
- [x] **K.2 YDB runs ledger** — `ops/runs.py` + SA key auth; tests
      `test_ops_runs_memory.py` / lifecycle
- [x] **K.3 Daily ₽ gate** — MSK day sum vs `YDBDOC_DAILY_BUDGET_RUB`
      (default 5000); tests in `test_ops_gates.py` / lifecycle
- [x] **K.4 Transcripts** — `TranscriptStore`; default **YDB** `run_objects`
      (§20.11); S3 impl behind `YDBDOC_TRANSCRIPT_BACKEND=s3`; TTL 14d +
      expired continue; tests `test_ops_transcripts_memory.py`
- [x] **K.5 Continue** — label `doc_continue`; parse `/ydbdoc continue`;
      max 3/PR; `run_doc_continue`; example workflow; tests
      `test_ops_continue_cmd.py`
- [ ] **K.6** Deploy (operator): merge + move `v0.1.0`; set ydb Actions
      vars/secrets; patch **real** `.github/workflows` in **ydb** (from
      `examples/` as reference — do not treat examples as auto-copied);
      create label `doc_continue`; smoke on a small docs PR

---

[← Memory Bank index](../../MEMORY_BANK.md)
