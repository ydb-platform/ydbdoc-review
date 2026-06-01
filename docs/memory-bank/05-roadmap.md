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

### Phase B.4 — Front matter (deferred to after Phase C)
- [ ] Parse YAML front matter
- [ ] Treat `title:` and `description:` as segments
- [ ] Pass through other keys (`vcsPath:`, `editable:` etc.)

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
- [ ] `navigation/paths.py` — detect `toc*.yaml` / redirect paths in repo (Phase F)

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
- [x] `translation/critic.py` — `run_critic`, `run_verify`, `apply_critic_fixes`, `review_with_critic`
- [x] Unit tests: `tests/unit/test_critic.py`
- [x] LLM smoke: `test_smoke_critic_json` in `test_llm_smoke.py` (local only)

#### D.5 — Per-file pipeline ✅ COMPLETE
- [x] `pipeline/types.py` — `FileTranslationResult`, verdict + usage summary
- [x] `pipeline/translate_file.py` — parse → translate → reinsert → critic → verify → render
- [x] Unit tests: `tests/unit/test_translate_file.py`

### Phase D — Translator + Critic ✅ COMPLETE

### Phase E — Validation heuristics
- [x] Placeholder count check (`validation/markers.py` — wired in translator)
- [x] CLI-token preservation (`validation/cli_tokens.py` — wired in translator)
- [ ] Length ratio (RU↔EN sane bounds)
- [ ] Cyrillic-in-EN detector
- [ ] Fence parity, heading parity, list-tab parity
- [ ] TOC / redirect merge validation (`validate_toc_merge`, `validate_redirect_merge`)

### Phase F — Pipeline & orchestrator
- [ ] Pre-analyze pass: which files need translation
- [x] Per-file pipeline glue (`pipeline/translate_file.py`, D.5)
- [ ] PR-level orchestrator: pair RU/EN, new/deleted/renamed
- [ ] Per-PR cache
- [ ] Sequential files, parallel batches (limit 3)

### Phase G — GitHub integration
- [ ] PR file enumeration (git diff vs base)
- [ ] `ydbdoc-review/pr-N` branch creation, push
- [ ] Short comment in source PR
- [ ] Open translation PR
- [ ] Post full report (translation + heuristics) in translation PR
- [ ] Verify mode: comment new report on each `doc_verify` run

### Phase H — Reporting
- [ ] Per-file verdict + issues
- [ ] Heuristics block (separate from critic issues)
- [ ] Cost / usage block
- [ ] Models + prompt version footer

### Phase I — Glue & shipping
- [ ] CLI (`run`, `verify`, `list-models`, `translate-file`, `extract`)
- [ ] Adapt Docker `entrypoint.sh`
- [ ] Move tag `v0.1.0` to merge commit on `main`
- [ ] Rewrite README
- [ ] Add `ARCHITECTURE.md` and `CONTRIBUTING.md`

---

---

[← Memory Bank index](../../MEMORY_BANK.md)
