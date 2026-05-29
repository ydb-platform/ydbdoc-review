# Memory Bank — ydbdoc-review v2 (doc-translate-ng branch)

> Living, opinionated document. Treat it as authoritative for design intent.  
> Last updated: end of Phase B (segmentation complete). Phase C (LLM client) starts next.

This file is **deliberately verbose**. It is written so that any developer or
AI assistant joining mid-project can reconstruct the full context: goals,
decisions, rationales, constraints, and conventions.

---

## 0. Pointers

```
.
├── src/ydbdoc_review/         # main Python package (v2 code)
│   ├── parsing/               # markdown → AST
│   ├── rendering/             # AST → markdown
│   ├── segmentation/          # AST ↔ translatable segments
│   ├── llm/                   # Yandex AI Studio client (Phase C)
│   ├── translation/           # translator + critic (Phase D)
│   ├── validation/            # post-translation heuristics (Phase D)
│   ├── pipeline/              # orchestration per-file and per-PR
│   ├── github/                # PR/branch/comment operations
│   ├── reporting/             # report builder
│   └── prompts/               # versioned prompts (v1/, ...) + glossary
├── tests/
│   ├── unit/                  # fast, no I/O
│   ├── integration/           # round-trip on real fixtures, LLM (local only)
│   └── fixtures/markdown_files/   # real YDB docs (committed)
├── scripts/                   # one-off utilities (fetch fixtures, smoke tests)
├── action.yml                 # GitHub Action manifest (unchanged from v1)
├── Dockerfile                 # Docker image for the action (unchanged from v1)
├── entrypoint.sh              # action entrypoint (unchanged from v1)
├── MEMORY_BANK.md             # this file
├── ARCHITECTURE.md            # human-developer-oriented architecture (TBD)
├── CONTRIBUTING.md            # developer guide (TBD)
└── README.md                  # user-oriented overview (rewrite at v2 release)
```

Important branch: **`doc-translate-ng`** — the v2 rewrite. To be merged into
`main` only after end-to-end tests pass on real PRs. Tag `v0.1.0` will be moved
(forward) to the merge commit at release time — the GitHub Action in the `ydb`
repo references this tag and the user (project owner) has limited ability to
change CI config in `ydb`. **Do not bump to `v0.2.0`** unless explicitly
decided otherwise.

---

## 1. Goal & non-goals

### 1.1. Goal

Build a reliable AST-based pipeline that translates YDB documentation between
Russian and English with **high quality and minimal hallucinations**, integrated
with GitHub Actions:

- Label `doc_translate` on a PR in `ydb-platform/ydb` → translate changed `.md`
  files, push to a separate branch `ydbdoc-review/pr-N`, open a separate
  translation PR.
- Label `doc_verify` on a translation PR → re-run QA (critic + heuristics) on
  the files as they currently exist on that branch, apply critic fixes, commit,
  comment with a fresh report.

The reviewer should be able to see a clear verdict per file and merge the
translation PR with minimal manual fixes.

### 1.2. Non-goals (v2)

- **Multi-language**: only RU↔EN. Other languages are out of scope.
- **Live translation memory across PRs**: we don't keep a persistent cache
  between PRs. (Per-PR caching is implemented; see §6.)
- **Auto-merge of the translation PR**: humans always decide.
- **Translating non-`.md` content**: code in fenced blocks, YAML config files,
  PNGs etc. stay untouched.
- **Translating `{% include %}`'d content**: includes are pointers; if the
  included file is in the PR, it gets its own translation.

---

## 2. Why v2 (lessons learned from v1)

v1 (on `main` before `doc-translate-ng`) had several pipelines side by side
(`masked`, `legacy_annotated`, `legacy_line_json`, file-with-plan). They tried
to translate large file chunks with regex-based protection. In practice they
produced:

- **Dropped or hallucinated whole sections**: in failed PR #41736 the entire
  `## Пакетная потоковая обработка` was missing in EN; entire `## Batch
  streaming processing` was invented with different content.
- **Broken fenced code blocks**: ` ```bash ` was injected mid-prose, code was
  pulled out of blocks.
- **Damaged CLI flags**: `--input-framing` became `--input--framing` (model
  "fixed" a typo that wasn't there).
- **Untranslated Cyrillic words** bleeding into English text ("узла" inside
  a link in EN file).
- **A `fix-diff` mechanism** (`find` / `replace`) that almost never matched
  because the critic did not quote text exactly — out of 4 critic-proposed
  fixes in #41736, **all 4 were rejected** as non-matching.

**Root cause**: regex-based protection on raw markdown + whole-file LLM I/O +
string-based critic fixes are architecturally fragile. **v2 fixes this at the
architecture level**:

1. There is **no second pipeline**. There is only AST.
2. **LLM never sees fenced code blocks or YFM tags as raw text** — they are
   AST structure.
3. **Translator works on segments** (paragraphs / cells / list items) in JSON
   I/O, not on whole files.
4. **Critic returns structured `suggested_text` keyed by `segment_id`** —
   applied 1:1 to the segment. No string find/replace.
5. **Validation is structural** (placeholder counts, CLI-token sets, JSON
   schema) plus heuristic (length, Cyrillic-in-EN, fence parity), and is
   automatic.

---

## 3. Architecture overview

### 3.1. End-to-end data flow

```
GitHub PR in ydb-platform/ydb (label: doc_translate)
        │
        ▼
GitHub Action → Docker container → entrypoint.sh
        │
        ▼
ydbdoc-review CLI (mode=run)
        │
        ▼
[1] Pair RU↔EN files from PR diff (git merge-base)
        │
        ▼
[2] Pre-analyze (cheap model): which files actually need translation?
        │
        ▼
[3] For each file that needs translation:
        ┌─────────────────────────────────────────────────────┐
        │ a. parse_markdown(text) → Document (AST)            │
        │ b. extract_segments(ast) → list[Segment]            │
        │ c. chunk_segments(segs) → list[Batch]               │
        │ d. for each batch (in parallel, limit=3):           │
        │      translator LLM (JSON I/O) → translations[]     │
        │      validate placeholders + CLI tokens             │
        │      retry per-segment on failure                   │
        │ e. reinsert_segments(ast, translations) → new AST   │
        │ f. critic LLM (JSON I/O) on the WHOLE file:         │
        │      issues[] with structured suggested_text        │
        │      apply suggested_text to segments               │
        │      re-validate critic pass → unresolved_issues[]  │
        │ g. heuristics: length, cyrillic-in-EN, fence parity │
        │ h. render_markdown(ast) → final markdown            │
        └─────────────────────────────────────────────────────┘
        │
        ▼
[4] Write all files, create branch `ydbdoc-review/pr-N`, push
[5] Open translation PR, post short comment in source PR
[6] Post full QA report (including heuristics) in translation PR
```

### 3.2. Hard architectural rules

1. **AST is the single source of truth.** No regex on raw markdown.
2. **LLM never sees raw code blocks or YFM tags.** Code blocks pass through
   untouched. YFM container tags are structural and never sent as text.
3. **The LLM only changes the inner text of segments.** Structure is
   reassembled from the original AST.
4. **No `find`/`replace` fixes from critic.** Critic returns structured
   `suggested_text` keyed by `segment_id`; we apply it 1:1.
5. **No whole-file LLM I/O** for translation; segments only, batched.
6. **Critic is in a different model family from translator** to avoid
   correlated blind spots.
7. **Job is green by default.** Errors at infrastructure level (no creds, push
   denied, code bug) fail. Translation quality issues don't fail the job;
   they are surfaced in the report. The user decides whether to merge.

---

## 4. Package layout

### 4.1. Current state (end of Phase B)

```
src/ydbdoc_review/
├── parsing/                       ✅ COMPLETE
│   ├── ast_types.py               IR pydantic models
│   ├── markdown_parser.py         markdown-it-py → IR
│   ├── inline_parser.py           re-parse a string as inline-only
│   └── yfm_plugins/
│       ├── variables.py           ✅ {{ var }}
│       ├── notes.py               ✅ {% note ... %}
│       ├── tabs.py                ✅ {% list tabs %}
│       ├── includes.py            ✅ {% include [text](path) %}
│       ├── conditionals.py        ✅ {% if %} … {% endif %}
│       ├── cuts.py                ✅ {% cut "title" %}
│       ├── terms.py               ✅ [*term-id]: definition / [*term-id]
│       ├── image_size.py          ✅ ![alt](src =WxH)
│       └── link_with_variable.py  ✅ [text]({{ var }}) and ![alt]({{ var }})
├── rendering/                     ✅ COMPLETE
│   └── markdown_renderer.py       IR → markdown (stable round-trip)
├── segmentation/                  ✅ COMPLETE
│   ├── types.py                   Segment, ProtectedInline, SegmentKind
│   ├── inline_protector.py        protect inline atoms with ⟦C1⟧/⟦L1⟧/...
│   ├── extractor.py               AST → list[Segment]
│   ├── reinsert.py                translations → updated AST
│   └── chunker.py                 segments → batches (char budget)
├── llm/                           ⏳ Phase C — STARTS NEXT
│   ├── client.py                  Yandex AI Studio (OpenAI-compatible)
│   ├── retry.py                   exponential backoff + model fallbacks
│   ├── structured.py              JSON parse + pydantic validation
│   ├── errors.py                  typed exceptions
│   └── usage.py                   token / cost tracking
├── translation/                   ⏳ Phase D
│   ├── translator.py              segments → translated segments
│   ├── critic.py                  AST → issues + suggested_text
│   ├── glossary.py                load YAML, inject into prompt
│   └── prompts.py                 prompt rendering, versioned
├── validation/                    ⏳ Phase D
│   ├── markers.py                 placeholder count check
│   ├── cli_tokens.py              --flag / $var preservation
│   └── heuristics.py              length ratio, cyrillic-in-en, fence parity
├── pipeline/                      ⏳ Phase E/F
│   ├── analyze.py                 pre-analyze: does this need translation?
│   ├── translate_file.py          full per-file pipeline
│   └── orchestrator.py            PR-level orchestration
├── github/                        ⏳ Phase G
│   ├── pr.py                      enumerate files, pair RU/EN
│   ├── branch.py                  ydbdoc-review/pr-N branch ops
│   └── comment.py                 source PR + translation PR comments
├── reporting/                     ⏳ Phase H
│   └── builder.py                 markdown report
├── config/                        ⏳ Phase C
│   ├── default.yaml               packaged defaults
│   └── loader.py                  YAML + env override
└── prompts/                       ⏳ Phase D
    ├── v1/
    │   ├── translate.md
    │   ├── critic.md
    │   ├── analyze.md
    │   └── system_common.md
    └── glossary.yaml              seed (~30-50 terms)
```

Legend: ✅ done · ⏳ pending · 🟡 partial.

### 4.2. Files outside the package

- `tests/fixtures/markdown_files/` — **committed** real YDB docs (RU+EN), 33
  files total, ~600 KB. Used for unit and integration round-trip tests. They
  are NOT regenerated automatically; refresh via `scripts/fetch_fixtures.sh`.
- `scripts/` — one-off utilities: `fetch_fixtures.sh`, `scan_yfm.py`,
  `inspect_yfm.py`, `smoke_yandex.py`, `debug_auth_table.py`.

---

## 5. AST model (IR) — full reference

Top-level: `Document { front_matter?, children: list[BlockNode] }`.

### 5.1. Block nodes

| Kind | Fields | Children |
|---|---|---|
| `paragraph` | — | inline list |
| `heading` | level (1-6), anchor (str?) | inline list |
| `fenced_code` | info (lang), content, fence_char, fence_len | — |
| `indented_code` | content | — |
| `thematic_break` | marker | — |
| `blockquote` | — | block list |
| `bullet_list` | marker (`-`/`*`/`+`), tight | list of `list_item` |
| `ordered_list` | start, delimiter (`.`/`)`), tight | list of `list_item` |
| `list_item` | marker | block list |
| `html_block` | content | — |
| `table` | header (TableRow), rows, aligns | — (cells contain inline) |
| `yfm_note` | note_type, title? | block list |
| `yfm_tabs` | variant (`tabs`/`tabs accordion`/`tabs radio`) | list of `yfm_tab` |
| `yfm_tab` | title (inline list) | block list |
| `yfm_include` | text, path, notitle (bool) | — (single-line) |
| `yfm_if` | — | list of `yfm_if_branch` |
| `yfm_if_branch` | condition (str?, None for else) | block list |
| `yfm_cut` | title | block list |
| `term_definition` | term_id | inline list |

### 5.2. Inline nodes

| Kind | Fields |
|---|---|
| `text` | content |
| `code` | content, marker_len |
| `em` | marker (`*`/`_`), children |
| `strong` | marker (`**`/`__`), children |
| `link` | href, title?, children |
| `image` | src, title?, alt, width?, height? |
| `html_inline` | content |
| `softbreak` | — |
| `hardbreak` | — |
| `yfm_variable` | name, raw (preserves original whitespace inside `{{ }}`) |
| `term_ref` | term_id |

### 5.3. Segments

A `Segment` is what we translate. See `src/ydbdoc_review/segmentation/types.py`.

- `id: str` — stable, `s0001`, `s0002`, …
- `kind: SegmentKind` — paragraph, heading, list_item, table_header_cell,
  table_body_cell, blockquote_paragraph, tab_title, term_definition.
- `path: list[str]` — breadcrumbs for LLM context, e.g.
  `["note:info", "table:header:col1"]`.
- `text: str` — markdown with `⟦K{n}⟧` placeholders.
- `placeholders: list[ProtectedInline]` — what each marker means.
- `ast_path: list[int | str]` — mixed-step path to navigate AST when
  re-inserting. Strings are typed markers (`"header"`, `"row"`, `"title"`).

### 5.4. Placeholder prefixes (LLM contract)

| Prefix | Meaning | Example |
|---|---|---|
| `C` | inline code | ` `--yaml` ` → `⟦C1⟧` |
| `L` | link (including text and url) | `[docs](http://x)` → `⟦L1⟧` |
| `I` | image | `![alt](img.png)` → `⟦I1⟧` |
| `H` | inline html | `<br/>` → `⟦H1⟧` |
| `V` | YFM variable | `{{ ydb-short-name }}` → `⟦V1⟧` |
| `T` | term ref | `[*cluster]` → `⟦T1⟧` |

**Counter is global per segment** (including inside `**bold**` / `*em*`).
This is critical: two `[link]` inside one paragraph must get different
indices `⟦L1⟧` and `⟦L2⟧`, never the same. Was a bug discovered in B.2,
fixed by sharing `_ProtectState` across recursion.

---

## 6. Key design decisions and trade-offs

### 6.1. Custom IR, not `SyntaxTreeNode`
markdown-it-py's `SyntaxTreeNode` doesn't guarantee round-trip stability and
is hard to extend with custom YFM nodes. We use a flat-token → custom IR
conversion in `markdown_parser.py`, plus pydantic for serialization.

### 6.2. Round-trip stability is "idempotent after one pass"
Byte-identical round-trip on arbitrary markdown is impossible. Contract:

> `render(parse(text))` may normalize formatting (spaces inside table cells,
> list marker style, headings), but `render(parse(render(parse(text))))`
> **must equal** `render(parse(text))`.

Enforced by every round-trip test.

### 6.3. Table cell pipe escaping
A literal `|` inside a table cell (e.g. `string \| list of strings`) must be
written as `\|`. Otherwise on the second parse pass markdown-it would split the
cell, drop the extra column, and lose data. Fixed in `_escape_table_cell`.
**Bug discovered on real file `auth.md`**, fixed in step 2.4.

### 6.4. YFM block plugins use `state.md.block.tokenize` for inner content
For containers like `{% note %}` and `{% list tabs %}`, we register a block
rule, find the matching closing tag (with nesting support), then call
markdown-it's own block tokenizer on the inner lines. This makes nested
constructs work for free.

### 6.5. `{{ variable }}` is an inline rule registered before `text`
This guarantees recognition before plain text consumes the braces.
`code_inline` and `fence` are not re-tokenized by markdown-it, so
`{{ name }}` inside `` `code` `` stays literal — verified by tests.

### 6.6. Source-mutating preprocessing for variables in URLs and image sizes
Two plugins use `core.ruler.before("normalize")` preprocessing to rewrite the
source before markdown-it tokenizes it, then `core.ruler.after("inline")` to
restore the original semantics on the resulting tokens:

- **link_with_variable**: rewrites `{{ var }}` inside `[...](...)` URLs to a
  URL-safe placeholder (`yfmvar-N-yfmvarend`), restores on
  `link_open.href` / `image.src` attributes.
- **image_size**: strips ` =WxH` from inside `![alt](src ...)`, stashes the
  size in `state.env`, attaches to image token as `meta.width` / `meta.height`.

Placeholders use alphanumerics + dashes only — valid URL chars; markdown-it
never interprets them.

### 6.7. Term references vs ordinary links
Term refs `[*name]` are inline tokens registered **before** `link` in the
inline ruler. They match only when the second character is `*`. Ordinary links
`[text](url)` are unaffected.

### 6.8. Unclosed YFM tags don't crash
If `{% note ... %}` lacks `{% endnote %}`, the rule returns `False` and the
opening line falls back to a plain paragraph. Tests verify this for every
container construct.

### 6.9. Globally unique placeholder counters per segment
See §5.4. The counter and placeholder list are kept in a shared
`_ProtectState` passed by reference through recursion in
`protect_inline`. Without this, nested constructs (link inside strong)
would reuse `⟦L1⟧` and collisions in the restore map would silently swap
links.

### 6.10. Mixed-type `ast_path` for re-insertion
For most nodes, `ast_path` is a list of int indices into `.children`. For
tables and tabs, we use typed string markers (`"header"`, `"row"`,
`"title"`) because their internal structure is not a flat children list:

- Table cell: `[..., "header", col_idx]` or `[..., "row", row_idx, col_idx]`.
- Tab title: `[..., tab_idx, "title"]`.
- Tab body block N: `[..., tab_idx, N]` (descend into `YfmTab.children`).

`_navigate_to_doc_index` in `reinsert.py` walks only int steps; typed-string
paths are decoded in `_set_inline_at_ast_path` per segment kind.

### 6.11. Hybrid LLM I/O strategy (Phase C/D)
Yandex AI Studio's OpenAI-compatible endpoint **does not support
`response_format={"type":"json_object"}`** (verified via documentation and
smoke test). We therefore use:

- **Translator**: JSON I/O. Smoke test (yandexgpt-5.1, deepseek-v32) shows
  models reliably return valid JSON when the prompt says "Return ONLY a JSON
  object". YandexGPT wraps in ` ```...``` ` fences sometimes; we strip them.
- **Critic**: JSON I/O. Returns `{verdict, issues}` schema.
- **Fallback** (if JSON parsing fails 3x): retry with delimited format
  `<<<S0001>>>...<<<END>>>`. Not implemented in MVP; in backlog.

### 6.12. Per-PR cache (intra-PR only)
Within a single PR run, identical segment texts (e.g. boilerplate paragraphs
included in multiple files) are translated once. The cache key is
`hash(text + path_context + role)`. Cache is in-memory only, discarded after
the run. No cross-PR cache.

### 6.13. Sequential files, parallel batches within file
Files are processed sequentially (predictable cost reporting, easier debug).
Batches inside a file are sent in parallel via `asyncio.gather` with a
concurrency limit of 3. This gives 3–5x speedup for large files without
overwhelming Yandex AI Studio.

### 6.14. Partial failure handling
If retries are exhausted for a segment/file:
- **Skip the file, continue with the rest of the PR.**
- Mark in the report: "Not translated due to API error".
- Translation PR is still created with the files that succeeded.
- This is preferred over failing the whole job (a single API hiccup
  shouldn't kill a 50-file PR).

### 6.15. Why config is YAML, not TOML
v1 used TOML. v2 uses YAML because the config has nested structures (per-role
models with fallback chains) and YAML is more readable for that. Migration is
trivial — there are only a handful of keys.

### 6.16. Why `MEMORY_BANK.md` is so verbose
This project is being co-developed by the human owner and an AI assistant
across many chat sessions. Context loss between sessions is a real risk.
The Memory Bank is the canonical handover document. Verbosity is intentional.

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
│   └── test_chunker.py
├── integration/                           on real fixtures, may include LLM
│   └── test_real_files_round_trip.py      parametrized over 66 fixtures
└── fixtures/markdown_files/               real .md from ydb-platform/ydb
    ├── ru/...
    └── en/...
```

Future:
- `tests/integration/test_llm_smoke.py` — real API calls. **Local only**, not in CI.
- `tests/integration/test_end_to_end.py` — full pipeline on a real file pair.

### 7.2. Counters (end of Phase B)

- **Unit**: 215 passed.
- **Integration**: 66 passed (all 33 fixture pairs round-trip stable).
- **Coverage goal**: 90%+ for `parsing/`, `segmentation/`, `rendering/`,
  `validation/`. Lower acceptable for `llm/`, `github/` because integration
  tests there are local-only.

### 7.3. How to run

```bash
pytest                                    # everything (unit + integration on fixtures)
pytest tests/unit/ -v                     # unit only
pytest tests/integration/ -v --tb=line    # integration on fixtures
pytest -k "tabs"                          # by keyword
pytest -m "not slow"                      # exclude slow markers
```

LLM tests (when added) will be marked `@pytest.mark.llm` and only run with
`pytest -m llm` (requires `.env` with credentials).

### 7.4. Fixture refresh

```bash
./scripts/fetch_fixtures.sh
python scripts/scan_yfm.py    # YFM-construct frequency report
```

Fixtures are committed and not auto-updated, so older versions stay reproducible.

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

### Phase C — LLM client ⏳ STARTS NEXT
- [ ] OpenAI-compatible client for Yandex AI Studio
  - Endpoint: `https://ai.api.cloud.yandex.net/v1`
  - Auth: `Api-Key <key>` via openai SDK
  - Model URI: `gpt://<folder_id>/<model_slug>`
- [ ] Config loader: YAML default + env override
- [ ] Retry with exponential backoff
- [ ] Model fallback chain on `Failed to get model`
- [ ] JSON output parsing with code-fence stripping
- [ ] Pydantic schema validation
- [ ] Usage tracking (input/output tokens, latency, retries)
- [ ] Smoke integration test (local only)

### Phase D — Translator + Critic
- [ ] Translator (per-batch, JSON I/O)
- [ ] Critic (per-file, structured `suggested_text`)
- [ ] Apply `suggested_text` to AST segments
- [ ] Re-validate critic pass → unresolved issues
- [ ] Glossary loader + full-glossary injection (subset is optimization)
- [ ] Prompt templates v1, packaged

### Phase E — Validation heuristics
- [ ] Placeholder count check (must match before/after)
- [ ] CLI-token preservation (`--flag`, `$var`, file paths)
- [ ] Length ratio (RU↔EN sane bounds)
- [ ] Cyrillic-in-EN detector
- [ ] Fence parity, heading parity, list-tab parity

### Phase F — Pipeline & orchestrator
- [ ] Pre-analyze pass: which files need translation
- [ ] Per-file pipeline glue
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

### 13.2. Schema (initial draft, will be finalized in Phase C)

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

Convention: `YDBDOC_<SECTION>_<KEY>` with dots → underscores.

Examples:
- `YDBDOC_LLM_MODELS_TRANSLATE_PRIMARY=deepseek-v4`
- `YDBDOC_LLM_TEMPERATURE=0.2`
- `YDBDOC_TRANSLATION_SEGMENTS_PER_BATCH_CHARS=2000`

### 13.4. Secrets (env only, never in YAML)

Order of precedence:
1. `YDBDOC_YC_FOLDER_ID`, `YDBDOC_YC_API_KEY`  — preferred new names.
2. `YANDEX_CLOUD_FOLDER_DOC_REVIEW`, `YANDEX_CLOUD_API_KEY_DOC_REVIEW` — v1 compat.
3. `YANDEX_CLOUD_FOLDER`, `YANDEX_CLOUD_API_KEY` — generic.
4. `YANDEX_CLOUD_FOLDER_2`, `YANDEX_CLOUD_SECRET_KEY` — current user's bashrc.

All four pairs supported simultaneously; first found wins.

GitHub: `GITHUB_TOKEN` (built-in), `GITHUB_PUSH_TOKEN`/`YDBDOC_PUSH_PAT`
(for fork pushes).

### 13.5. `.env.example` (committed)

```
# Yandex AI Studio
YDBDOC_YC_FOLDER_ID=
YDBDOC_YC_API_KEY=

# Optional model overrides
# YDBDOC_LLM_MODELS_TRANSLATE_PRIMARY=
# YDBDOC_LLM_MODELS_CRITIC_PRIMARY=

# GitHub (for local PR operations)
# GITHUB_TOKEN=
```

User copies to `.env` and fills in. `.env` is gitignored.

---

## 14. Glossary

### 14.1. Source of truth

`src/ydbdoc_review/prompts/glossary.yaml` — committed, hand-maintained for now.

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

## 15. Pipeline data flow (detailed, Phase D+)

### 15.1. Per-file pipeline

```
INPUT: source_text (str), source_lang, target_lang, glossary, models

1. PARSE
   doc = parse_markdown(source_text)

2. EXTRACT
   segments = extract_segments(doc)
   # Each segment has id, kind, path, text (with ⟦C1⟧ markers), placeholders, ast_path.

3. CHUNK
   batches = chunk_segments(segments, max_chars=4000)

4. TRANSLATE (parallel batches, limit 3)
   async for batch in batches:
       request = build_translate_prompt(batch, glossary, path_context)
       response = await llm_client.chat(translate_model, request)
       translations[batch] = parse_json(response)
       validate_placeholders(batch, translations[batch])
       validate_cli_tokens(batch, translations[batch])
       # On failure: retry per-segment

5. REINSERT (preserves AST structure)
   translated_doc = reinsert_segments(doc, segments, translations)

6. CRITIC PASS 1 (whole file)
   translated_text = render_markdown(translated_doc)
   critic_request = build_critic_prompt(source_text, translated_text, segments, glossary)
   critic_response = await llm_client.chat(critic_model, critic_request)
   issues = parse_critic_response(critic_response)
   # issues = [{segment_id, severity, category, comment, suggested_text}]

7. APPLY CRITIC FIXES
   for issue in issues:
       if issue.suggested_text:
           translations[issue.segment_id] = issue.suggested_text
   translated_doc = reinsert_segments(doc, segments, translations)

8. CRITIC PASS 2 (re-validate)
   translated_text = render_markdown(translated_doc)
   verify_response = await llm_client.chat(critic_model, build_verify_prompt(...))
   unresolved = parse_critic_response(verify_response).issues

9. HEURISTICS (deterministic)
   warnings = run_heuristics(source_text, translated_text)
   # length_ratio, cyrillic_in_en, fence_parity, heading_parity, etc.

10. RENDER
    final_text = render_markdown(translated_doc)

OUTPUT: final_text, file_report = {
    file_path,
    verdict,                          # ok / warnings / blocked
    critic_issues,                    # initial issues
    unresolved_issues,                # after critic pass 2
    heuristic_warnings,
    cost,                             # tokens + latency
    models_used,
    prompt_version,
}
```

### 15.2. PR-level orchestrator

```
INPUT: pr_number, source_repo, target_branch_base

1. ENUMERATE
   changed_md = github.list_changed_md_files(pr_number, target_branch_base)
   pairs = pair_ru_en(changed_md)
   # pairs: [{ru_path, en_path, ru_exists, en_exists, ru_changed, en_changed}]

2. PRE-ANALYZE (cheap model, batched)
   needs_translate = pre_analyze_pairs(pairs, analyze_model)
   # For each pair: {action: translate_to_en | translate_to_ru | skip}

3. PER-FILE TRANSLATION (sequential)
   per_pr_cache = {}
   reports = []
   for pair in needs_translate:
       try:
           translated, report = translate_file(
               source_text=read(pair.source),
               target_lang=pair.target_lang,
               cache=per_pr_cache,
           )
           write(pair.target, translated)
           reports.append(report)
       except APIError as e:
           reports.append(failed_report(pair, e))
           continue  # don't fail the whole PR

4. GIT
   branch = f"ydbdoc-review/pr-{pr_number}"
   git.create_branch_from(source_pr_head_ref, branch)
   git.commit_all(branch, message=build_commit_message(reports))
   git.push(branch)

5. GITHUB
   tr_pr = github.open_pr(
       head=branch,
       base=source_pr_head_ref,  # i.e. the PR's HEAD branch
       title=f"Auto-translate docs from PR #{pr_number}",
       body=build_pr_description(reports),
   )
   github.post_comment(
       pr_number,
       body=f"Translation PR ready: #{tr_pr.number}. See report there.",
   )
   github.post_comment(
       tr_pr.number,
       body=build_full_report(reports, heuristics, cost),
   )

OUTPUT: exit code 0 unless infrastructure failure.
```

### 15.3. Verify mode

```
INPUT: translation_pr_number

1. Discover source PR number from translation PR description
2. Read ru + en files from translation PR head (NOT main)
3. Run critic + heuristics (no translator)
4. Apply critic fixes (suggested_text per segment_id)
5. If any fixes applied: commit + push to translation PR branch
6. Post a NEW comment on the translation PR with the report
   (do NOT replace previous; history is valuable)
```

---

## 16. PR-level behavior

### 16.1. File pairing

For each changed `.md` under `ydb/docs/`:

- `ydb/docs/ru/X` ↔ `ydb/docs/en/X` (mirror).
- `ydb/docs/_includes/Y` — language-neutral; not translated.

If RU changed and EN did not → translate to EN (overwrite).
If EN changed and RU did not → translate to RU (overwrite).
If both changed:
  - Pre-analyze decides: if they look like a synced manual edit, skip
    translation, but still run critic.
  - Otherwise: re-translate from source language (RU is default source).
If RU exists but EN doesn't → create EN from RU.
If EN exists but RU doesn't → create RU from EN.

### 16.2. New / deleted / renamed

- **New file in RU**: create EN.
- **Deleted file in RU**: also delete EN.
- **Renamed file**: not auto-detected from git rename info in MVP;
  treat as delete+add. (Tracked in backlog if needed.)

### 16.3. Translation branch and PR

- Branch: `ydbdoc-review/pr-<source_pr_number>`.
- Branch base: the HEAD of the source PR (not `main`).
- One commit per run. Message:
  ```
  Auto-translate docs from PR #N

  Translated K files (X new, Y updated):
  - <path>
  ...
  Translator: <model>
  Critic: <model>
  ydbdoc-review v0.2.0
  ```
- Translation PR base: the source PR's HEAD branch.
- Translation PR title: "Auto-translate docs from PR #N".
- Translation PR body: short summary + link to source PR.
- Committer/author: GitHub Actions bot (uses `GITHUB_TOKEN`).

### 16.4. Verify mode commits

- When critic proposes fixes:
  ```
  Apply critic fixes from doc_verify run on <timestamp>

  Critic: <model>
  Fixed segments: K
  ydbdoc-review v0.2.0
  ```

---

## 17. Reporting format

### 17.1. Short comment in source PR (after `doc_translate`)

```markdown
🤖 **ydbdoc-review** — перевод готов

| | |
|---|---|
| Translation PR | #M |
| Файлов переведено | 5 (3 новых, 2 обновлено) |
| Статус QA | 🟡 4 OK, 1 требует ревью |
| Время | 2m 14s |
| Стоимость | ~$0.42 |

👉 Полный отчёт в translation PR #M.
```

### 17.2. Full report in translation PR (after `doc_translate` or `doc_verify`)

```markdown
🤖 **ydbdoc-review** — отчёт #1 (doc_translate, 2024-11-05 14:23 UTC)

## Вердикт: 🟡 4 OK, 1 требует ревью

| Файл | Статус | Critic issues | Heuristic warnings |
|---|---|---|---|
| `…/foo.md` | 🟢 OK | 0 | 0 |
| `…/bar.md` | 🟢 OK | 0 | 1 (length ratio borderline) |
| `…/new.md` | 🟡 Warnings | 2 fixed, 0 unresolved | 1 (cyrillic in EN) |

## Сводка
- Сегментов переведено: 234 (auto-translated)
- Critic fixes auto-applied: 12
- Critic fixes unresolved: 0
- Heuristic warnings: 3
- Retry total: 3 (1.3%)
- Время: 2m 14s
- Tokens: translator 12,341/4,102; critic 8,221/1,503
- Cost: ~$0.42
- Models: translator=`yandexgpt-5.1`, critic=`qwen3.6-35b-a3b`
- Prompt version: v1

## Детали по файлам

### 🟡 `…/new.md`

**Critic issues (auto-applied: 2, unresolved: 0)**
- `s0042` (paragraph, in "Usage examples")
  - Category: terminology
  - "command" → "директива" (glossary mismatch)
  - 🟢 auto-applied

**Heuristic warnings**
- `cyrillic_in_en`: 1 occurrence at line 87 ("Sample")

<details>
<summary>Glossary used (12 entries)</summary>

- параметризованный запрос → parameterized query
- …
</details>

---

Generated by ydbdoc-review v0.2.0
```

### 17.3. Subsequent `doc_verify` runs

Each `doc_verify` run posts a NEW comment of the same format, with a header
`🤖 ydbdoc-review — отчёт #N (doc_verify, <timestamp>)`. Previous comments
remain visible for history.

---

## 18. Prompts (will be filled in Phase D)

### 18.1. Versioning

```
src/ydbdoc_review/prompts/
├── v1/
│   ├── system_common.md       Shared system instructions
│   ├── translate.md           Translator prompt template
│   ├── critic.md              Critic prompt template
│   ├── verify.md              Verify pass prompt template
│   └── analyze.md             Pre-analyze prompt template
└── glossary.yaml              Glossary (shared across versions)
```

Each prompt template is markdown with `{placeholders}` filled at runtime.

### 18.2. Versioning policy

- Current version recorded in `config/default.yaml` → `prompts.version`.
- Old versions kept indefinitely for reproducibility.
- New version (`v2/`) created when behavior changes are non-trivial.
- The report footer always includes the prompt version used.

### 18.3. Common system instructions (sketch)

```
You are a professional technical translator working on YDB documentation.

CRITICAL RULES:
- Translate ONLY the provided segments. Do not add, remove, or merge segments.
- Preserve every placeholder ⟦X{n}⟧ exactly as-is. Do not translate or modify them.
- Preserve CLI flags exactly: --yaml stays --yaml; do not split into "-- yaml".
- Preserve identifiers, file paths, URLs, and code snippets verbatim.
- Use the glossary entries provided. Match terms even across morphological forms.
- Never use em-dash or en-dash where a hyphen is required (e.g. in --flag).
- Return ONLY the JSON object requested. No prose, no markdown fences.

GLOSSARY:
{glossary_yaml}
```

Exact prompts will be finalized when Phase D starts.

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

### 19.3. Debug log file

CLI flag `--debug-log <path>` writes full LLM request/response bodies to a
file for offline analysis. Off by default to avoid leaking content.

---

## 20. Cost tracking

### 20.1. Per-call tracking

Every `llm.chat()` call records:
- `model`, `input_tokens`, `output_tokens`, `latency_ms`, `retries`, `success`.

### 20.2. Aggregation per PR

Sum tokens and approximate cost. Cost calculation uses a hard-coded price
table per model slug (updated manually). Yandex AI Studio doesn't return
prices in headers as of writing.

### 20.3. Reporting

Cost block appears in the full report (translation PR) and the short summary
(source PR).

### 20.4. Backlog: persistent cost log

`docs-internal/cost-log.md` (in `ydbdoc-review` repo) maintained by a script
that appends one line per PR run. Not in MVP.

---

## 21. Glossary of terms used in this Memory Bank

- **AST / IR**: our pydantic representation of a parsed markdown document.
- **Segment**: a translatable unit extracted from AST (a paragraph, a heading,
  a table cell, a list item, etc.).
- **Placeholder / marker**: `⟦C1⟧`, `⟦L1⟧`, etc., representing a protected
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

---

**End of Memory Bank.**
