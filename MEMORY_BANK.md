# Memory Bank — ydbdoc-review v2 (doc-translate-ng branch)

> Living document. Updated after each significant step.  
> Last updated: Step 2.5 — `{% include %}` block plugin.

---

## 1. Goal

Build a reliable AST-based pipeline that translates YDB documentation between
Russian and English with **high quality and minimal hallucinations**, integrated
with GitHub Actions:

- `doc_translate` label on a PR → translate changed `.md` files, open a separate
  translation PR.
- `doc_verify` label on a PR with translations → run the same QA pass without
  re-translating.

The reviewer should be able to see a clear verdict per file and merge with
minimal manual fixes.

---

## 2. Why v2 (rewrite)

The v1 pipeline (`main` branch as of fork point) had several pipelines side by side
(`masked`, `legacy_annotated`, `legacy_line_json`, file-with-plan) and tried to
translate large file chunks with regex-based protection. In practice this
produced:

- Dropped or hallucinated whole sections (entire `## Batch streaming processing`
  missing in failed PR #41736).
- Broken fenced code blocks (` ```bash ` injected into prose).
- Damaged CLI flags (`--input--framing` instead of `--input-framing`).
- Untranslated Cyrillic words bleeding into English text.
- A `fix-diff` mechanism (`find` / `replace`) that almost never matched because
  the critic did not quote text exactly.

Root cause: **regex-based protection on raw markdown** + **whole-file LLM I/O**
+ **string-based critic fixes** are architecturally fragile. v2 fixes this at
the architecture level — there is no second pipeline. There is only AST.

---

## 3. Architecture overview (v2)

```
PR (label doc_translate)
   │
   ▼
List of .md files (diff vs base)
   │
   ▼
Per-file pipeline:
  1. parse_markdown(text)              → Document (IR/AST)
  2. extract_segments(ast)             → list[Segment] (text-bearing leaves only)
  3. protect_inline(seg)               → seg with ⟦MARKER⟧ placeholders
  4. chunk_segments(segs)              → list[Batch]
  5. for batch in batches:
        translate via LLM (structured JSON I/O)
        validate markers + CLI tokens + JSON shape
        retry per-segment on failure
  6. critic_pass(ast_with_translations) → issues[] with structured suggested_text
  7. apply_suggested_text(issues)       → updated segments
  8. critic_revalidate                  → unresolved_issues[] (report-only)
  9. restore_inline + rebuild AST
 10. render_markdown(ast)              → final markdown file
 11. write to ydbdoc-review/pr-N branch
   │
   ▼
Comment in source PR + new translation PR opened
```

Hard rules:

- **The LLM never sees raw fenced code blocks or YFM tags.** Code blocks pass
  through untouched; YFM container tags are part of AST structure, not text.
- **The LLM never modifies the AST structure** — only the inner text of
  segments. Structure is reassembled from the original AST.
- **No `find`/`replace` fixes from critic.** Critic returns structured
  `suggested_text` keyed by `segment_id`; we apply it 1:1.
- **No whole-file LLM I/O** for translation; segments only, batched.
- **AST is the single source of truth**, not regex.

---

## 4. Package layout

```
src/ydbdoc_review/
├── parsing/
│   ├── ast_types.py          ✅ pydantic models for IR
│   ├── markdown_parser.py    ✅ markdown-it-py → IR
│   └── yfm_plugins/
│       ├── variables.py      ✅ {{ var }}
│       ├── notes.py          ✅ {% note ... %}…{% endnote %}
│       ├── tabs.py           ✅ {% list tabs %}…{% endlist %}
│       ├── includes.py       ✅ {% include [text](path) %}
│       ├── cuts.py           ⏳ {% cut "title" %}…{% endcut %}
│       └── conditionals.py   ⏳ {% if ... %}…{% endif %}
├── rendering/
│   └── markdown_renderer.py  ✅ IR → markdown (stable round-trip)
├── segmentation/             ⏳ AST → translatable units
│   ├── extractor.py
│   ├── inline_protector.py
│   └── chunker.py
├── translation/              ⏳ LLM-driven translation + critic
│   ├── translator.py
│   ├── critic.py
│   ├── glossary.py
│   └── prompts.py
├── llm/                      ⏳ Yandex AI Studio client (OpenAI-compatible)
│   ├── client.py
│   ├── retry.py
│   └── structured.py
├── validation/               ⏳ post-translation checks
│   ├── markers.py
│   ├── cli_tokens.py
│   └── heuristics.py
├── pipeline/                 ⏳ orchestration
│   ├── analyze.py
│   ├── translate_file.py
│   └── orchestrator.py
├── github/                   ⏳ PR / branch / comment
│   ├── pr.py
│   ├── branch.py
│   └── comment.py
└── reporting/                ⏳ final report builder
    └── builder.py
```

Legend: ✅ done · ⏳ pending · 🟡 partial.

---

## 5. AST model (IR) — current

Top-level: `Document { front_matter?, children: list[BlockNode] }`.

### Block nodes
- `paragraph` — children: list[InlineNode]
- `heading` — level, anchor (`{#…}`), children
- `fenced_code` — info, content, fence_char, fence_len
- `indented_code`
- `thematic_break`
- `blockquote`
- `bullet_list` / `ordered_list` — children: list[ListItem]
- `list_item`
- `html_block`
- `table` — header (TableRow), rows, aligns; cells contain inline nodes
- `yfm_note` — note_type, optional title, children
- `yfm_tabs` — variant (`tabs` / `tabs accordion` / `tabs radio`), children: list[YfmTab]
- `yfm_tab` — title (inline), children (block)
- `yfm_include` — text, path, notitle (single-line directive, no children)

### Inline nodes
- `text`, `code`, `em`, `strong`, `link`, `image`, `html_inline`,
  `softbreak`, `hardbreak`
- `yfm_variable` — name, raw (preserves original whitespace inside `{{ }}`)

---

## 6. Key design decisions and trade-offs

### 6.1. Custom IR instead of `SyntaxTreeNode`
markdown-it-py provides `SyntaxTreeNode`, but it does not guarantee round-trip
stability and is hard to extend with custom YFM nodes. We use a flat-token →
custom IR conversion in `markdown_parser.py`, plus pydantic for serialization.

### 6.2. Round-trip stability is "idempotent after one pass"
Byte-identical round-trip on arbitrary markdown is impossible (markdown allows
many equivalent forms: `*a*`/`_a_`, `-`/`*`/`+`, trailing newline counts, etc.).
Our contract:

> `render(parse(text))` may normalize formatting (e.g. spaces inside table
> cells, list marker style, headings), but `render(parse(render(parse(text))))`
> **must equal** `render(parse(text))`.

This is enforced by every round-trip test.

### 6.3. Table cell pipe escaping
A literal `|` inside a table cell (e.g. `string \| list of strings`) must be
written as `\|`. Otherwise on the second parse pass markdown-it would split the
cell, drop the extra column, and lose data. Fixed in `_escape_table_cell`.

### 6.4. YFM block plugins use `state.md.block.tokenize` for inner content
For containers like `{% note %}` and `{% list tabs %}`, we don't try to parse
the inner content ourselves. We register a block rule, find the matching
closing tag (with nesting support), then call markdown-it's own block
tokenizer on the inner lines. This makes nested constructs work for free.

### 6.5. `{{ variable }}` is an inline rule registered before `text`
This guarantees it's recognized before plain text consumes the braces. It also
respects `code_inline` and `fence` (which markdown-it doesn't re-tokenize), so
`{{ name }}` inside `` `code` `` stays literal — verified by tests.

### 6.6. Known limitation: `[text]({{ var }})` URL not recognized
markdown-it's link rule does not accept `{` in URLs. As of v0.2 this falls back
to plain text. Round-trip is stable, but we cannot model the link semantically.
**TODO**: write a custom link rule (see Section 9).

### 6.7. Unclosed YFM tags don't crash
If `{% note ... %}` lacks `{% endnote %}`, the rule returns `False` and the
opening line falls back to a plain paragraph. Tests verify this.

---

## 7. Tests

### Layout
```
tests/
├── unit/                                  fast, no I/O
│   ├── test_parser_round_trip.py          plain markdown round-trip
│   ├── test_yfm_variables.py              {{ var }}
│   ├── test_yfm_notes.py                  {% note %}
│   └── test_yfm_tabs.py                   {% list tabs %}
├── integration/                           on real fixture files
│   └── test_real_files_round_trip.py      parametrized over fixtures/markdown_files/**
└── fixtures/
    └── markdown_files/                    real .md downloaded from ydb-platform/ydb
        ├── ru/...
        └── en/...
```

### Current counters (Step 2.5)
- Unit: 103 passed, 1 xfail
- Integration: 66 passed (all real files round-trip stable)

Real file count includes both RU and EN variants of:
glossary, transactions, configuration-v2, cluster-expansion,
deployment-configuration-{v1,v2}, deployment-preparation,
spring-data-jdbc, quickstart, parameterized-query-execution, sql,
create-streaming-query, declare, primitive, auth, topic,
system-tablet-backup (RU only).

### How to run
```bash
pytest                                    # all tests
pytest tests/unit/ -v                     # unit only
pytest tests/integration/ -v --tb=line    # integration on real files
pytest -k "tabs"                          # by keyword
```

### Fixture refresh
```bash
./scripts/fetch_fixtures.sh
python scripts/scan_yfm.py                # YFM-construct frequency report
```

---

## 8. Roadmap

### Phase A — Parser/renderer foundation
- [x] 2.1 markdown parser + renderer + round-trip on synthetic markdown
- [x] 2.2 YFM `{{ variable }}` plugin
- [x] 2.3 YFM `{% note %}` plugin
- [x] 2.4 YFM `{% list tabs %}` plugin + table pipe-escape fix
- [x] 2.5 YFM `{% include %}` plugin (inline include directive)
- [ ] 2.6 YFM `{% if %}…{% endif %}` plugin (conditionals)
- [ ] 2.7 YFM `{% cut "title" %}…{% endcut %}` plugin
- [ ] 2.8 Term definitions `[*term]: definition`
- [ ] 2.9 Image with size attribute `![alt](src =100x100)`
- [ ] 2.10 Custom link rule for `{{ var }}` inside URLs (resolves TODO from 2.2)

### Phase B — Segmentation
- [ ] Extract translatable segments from AST (paragraphs, headings, list items,
      table cells, blockquote paragraphs, note body, tab body).
- [ ] Inline protector: replace code_inline / link / image / variable / html_inline
      with `⟦P1⟧`-style markers, keep mapping.
- [ ] Chunker: group segments into batches with character budget; never split
      a segment; respect "must-stay-together" hints (heading + first paragraph,
      siblings in same list, cells of same row).
- [ ] Identity test: extract segments → fake-translate with `identity` → re-insert
      → render → equals original.

### Phase C — LLM client
- [ ] OpenAI-compatible client for Yandex AI Studio (reuse v1 logic from `llm.py`).
- [ ] Structured output: prefer `response_format={"type": "json_object"}` with
      pydantic validation; fallback to lenient JSON parsing + retry.
- [ ] Per-segment retry on validation failure.
- [ ] Model fallback chain on `Failed to get model`.

### Phase D — Translator + Critic
- [ ] Translator: batch of segments → translated batch (JSON I/O).
- [ ] Critic: same batch + translations → structured `issues[]` with
      `segment_id`, `severity`, `category`, `comment`, `suggested_text`.
- [ ] Apply `suggested_text` directly to segments (no string find/replace).
- [ ] Re-validate pass: critic confirms or marks unresolved.

### Phase E — Glossary
- [ ] Parse YDB glossary page (https://ydb.tech/docs/ru/concepts/glossary) into
      YAML: `[{ru, en, aliases, do_not_translate}]`.
- [ ] Inject glossary hits relevant to the segment into the LLM prompt.

### Phase F — Pipeline & orchestrator
- [ ] Pre-analyze pass: determine which files actually need translation.
- [ ] Per-file pipeline.
- [ ] PR-level orchestrator: pair RU/EN, new/deleted/renamed handling.

### Phase G — GitHub integration
- [ ] PR file enumeration.
- [ ] `ydbdoc-review/pr-N` branch creation, push.
- [ ] Comment in source PR + open translation PR.

### Phase H — Reporting
- [ ] New report format (verdict + per-file details + ergonomics).

### Phase I — Glue & shipping
- [ ] CLI (`run`, `verify`, `list-models`).
- [ ] Adapt Docker `entrypoint.sh`.
- [ ] Move tag `v0.1.0` (or release `v0.2.0`).
- [ ] Rewrite README.

---

## 9. TODO / Backlog

- **YFM-link-with-variable**: custom markdown-it link rule that accepts
  `{{ var }}` inside URLs. Currently `[text]({{ var }})` parses as plain text.
  Test in `test_yfm_variables.py::test_variable_in_link_url` is `xfail`.
- **Strikethrough rendering**: GFM strikethrough is enabled in the parser but
  not modelled in IR; tokens are currently dropped silently. Add `InlineStrike`
  node.
- **Hard line breaks** rendered as `␠␠\n`; some authors prefer `\\`. Verify
  YDB's actual usage and decide.
- **Front matter** is round-tripped as a raw string. If we need to translate
  YAML values like `description:` later, parse it as YAML.
- **Indented code blocks** are rendered with 4-space indent. Check whether YDB
  uses them; if not, keep but don't optimize.
- **Image attributes** `=100x100` and `{ width="100" }` — not modelled.

---

## 10. Working agreements (with the AI assistant)

- One step at a time. Each step produces something testable.
- Tests are mandatory; we never accept "it works on my machine".
- Round-trip on real files is the canonical signal: it must stay green forever.
- Memory Bank is updated after every meaningful step.
- Code is delivered as copy-pasteable blocks; the user applies, runs, reports.
- The AI does **not** have GitHub write access; the user commits and pushes.

---

## 11. Environment

- Python 3.11+ (CI), Python 3.14 ok locally (current dev env).
- Tooling: `pytest`, `pytest-mock`, `pytest-cov`, `ruff`, `mypy`.
- Dependencies pinned via `pyproject.toml`; `requirements.txt` mirrors for Docker.

YDB-specific:
- LLMs accessed via Yandex AI Studio (OpenAI-compatible endpoint
  `https://ai.api.cloud.yandex.net/v1`).
- Models configured in `ydbdoc-review.toml` (`[models].check`, `.translate`,
  `.translation_verify`) and env overrides (`YDBDOC_MODEL_*`).
- Critic family must differ from translator family (avoid both being Yandex).

---

## 12. Glossary references (planned)

Source of truth: https://ydb.tech/docs/ru/concepts/glossary?version=main  
TBD: extracted YAML at `src/ydbdoc_review/translation/glossary.yaml`.

Sample entries to seed (when Phase E starts):
- параметризованный запрос → parameterized query
- узел → node
- кеш → cache
- кластер → cluster
- таблетка → tablet
- транзакция → transaction
- координатор → coordinator

(do_not_translate: YDB, SQL, JSON, CSV, TSV, CLI, gRPC, YQL, OAuth, JWT, …)

