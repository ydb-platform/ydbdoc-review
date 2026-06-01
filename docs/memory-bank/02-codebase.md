# Memory Bank — Codebase reference

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

---

## 4. Package layout

### 4.1. Current state (Phase C complete)

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
├── llm/                           ✅ COMPLETE
│   ├── client.py                  YandexLLMClient — OpenAI SDK, retry, fallback
│   ├── retry.py                   exponential backoff + error classification
│   ├── structured.py              JSON parse + fence strip + pydantic validate
│   ├── errors.py                  typed exceptions
│   └── usage.py                   token / cost tracking
├── translation/                   ⏳ Phase D — IN PROGRESS
│   ├── glossary.py                ✅ load YAML + format for prompts (D.1)
│   ├── prompts.py                 ✅ template load/render + message builders (D.2)
│   ├── schemas.py                 ✅ translator JSON pydantic models (D.3)
│   ├── translator.py              ✅ per-batch segment translation (D.3)
│   └── critic.py                  ✅ per-file review + apply fixes (D.4)
├── navigation/                    ✅ scoped TOC + redirect merge (D.1.5)
│   ├── toc.py                     parse, diff scope, merge, validate
│   └── redirects.py               Diplodoc redirect list — same pattern
├── validation/                    ⏳ Phase D/E
│   ├── markers.py                 ✅ placeholder parity (D.3)
│   ├── cli_tokens.py              ✅ --flag / $var preservation (D.3)
│   └── heuristics.py              length ratio, cyrillic-in-en, fence parity
├── pipeline/                      ✅ Phase F
│   ├── translate_file.py          ✅ per-file pipeline (D.5)
│   ├── pairs.py                   ✅ RU/EN pairing (F)
│   ├── analyze.py                 ✅ pre-analyze plans (F)
│   ├── orchestrator.py            ✅ run_pr_translation (F)
│   └── types.py                   result dataclasses
├── github/                        ✅ Phase G
│   ├── client.py                  GitHub REST (requests)
│   ├── git_ops.py                 local git diff / branch / commit / push
│   ├── pr.py                      PR context, file changes, pair loading
│   ├── workflow.py                run_doc_translate, run_doc_verify
│   └── errors.py                  typed GitHub errors
├── reporting/                     ✅ Phase H
│   └── builder.py                 markdown reports (§17 format)
├── config/                        ✅ COMPLETE
│   ├── default.yaml               packaged defaults
│   └── loader.py                  Pydantic schema + YAML + env override
├── cli.py                         ✅ Phase I — Typer CLI + __main__.py
└── prompts/                       ✅ v1 templates + glossary (packaged)
```

Legend: ✅ done · ⏳ pending · 🟡 partial.

### 4.2. Files outside the package

- `tests/fixtures/markdown_files/` — **committed** real YDB docs (RU+EN), 33
  files total, ~600 KB. Used for unit and integration round-trip tests. They
  are NOT regenerated automatically; refresh via `scripts/fetch_fixtures.sh`.
- `scripts/` — one-off utilities: `fetch_fixtures.sh`, `scan_yfm.py`,
  `inspect_yfm.py`, `smoke_yandex.py`, `debug_auth_table.py`.

---

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

---

[← Memory Bank index](../../MEMORY_BANK.md)
