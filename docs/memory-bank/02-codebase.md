# Memory Bank — Codebase reference

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

---

## 4. Package layout

### 4.1. Current state (Phase I complete)

```
src/ydbdoc_review/
├── parsing/                       ✅ COMPLETE (+ B.4 front matter)
│   ├── ast_types.py               IR pydantic models
│   ├── markdown_parser.py         markdown-it-py → IR
│   ├── inline_parser.py           re-parse a string as inline-only
│   ├── front_matter.py            YAML title/description segments
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
│   └── markdown_renderer.py       IR → markdown (stable round-trip; §6.60 backtick padding)
├── segmentation/                  ✅ COMPLETE
│   ├── types.py                   Segment, ProtectedInline, SegmentKind
│   ├── inline_protector.py        protect inline atoms with ⟦C1⟧/⟦U1⟧/...
│   ├── extractor.py               AST → list[Segment] (incl. front matter)
│   ├── reinsert.py                translations → updated AST
│   ├── placeholder_align.py       RU/EN placeholder renumbering + atom legend (§6.55)
│   └── chunker.py                 segments → batches (char budget)
├── llm/                           ✅ COMPLETE
│   ├── client.py                  YandexLLMClient — OpenAI SDK, retry, fallback
│   ├── retry.py                   exponential backoff + error classification
│   ├── structured.py              JSON parse + fence strip + pydantic validate
│   ├── errors.py                  typed exceptions
│   └── usage.py                   token / cost tracking
├── translation/                   ✅ COMPLETE (Phase D)
│   ├── glossary.py                load YAML + format for prompts (D.1)
│   ├── prompts.py                 template load/render + message builders (D.2)
│   ├── schemas.py                 translator/critic JSON pydantic models (D.3–D.4)
│   ├── translator.py              per-batch translation + repair-pass trigger (D.3)
│   ├── repair.py                  focused LLM repair after validation failure
│   ├── critic_retranslate.py      critic-feedback segment retranslate (§6.66)
│   ├── manual.py                  ManualAction for fail-soft table cells
│   └── critic.py                  batched critic/verify + verdict alias normalize; regression guard on auto-apply (§6.53)
├── navigation/                    ✅ scoped TOC + redirect merge (inline + block toc)
│   ├── toc.py                     parse (href + include.path), nested indent, merge, validate (§6.62–§6.63)
│   ├── redirects.py               Diplodoc redirect list — same pattern
│   └── paths.py                   toc/redirect path detection
├── validation/                    ✅ COMPLETE (Phase E)
│   ├── markers.py                 placeholder multiset + cross-lang drift checks (§6.54–§6.57)
│   ├── placeholder_drift.py       drop spurious critic issues; skipped dedupe (§6.54–§6.61)
│   ├── placeholder_roles.py       semantic V/U placement (link dest vs prose)
│   ├── placeholder_repair.py      restore ⟦X⟧; swap V↔U; clause reorder (s0077)
│   ├── homoglyphs.py              EN postprocess: homoglyphs, fence placeholders, MD031
│   ├── markdown_layout.py         `fix_blanks_around_fences` (markdownlint MD031)
│   ├── fence_integrity.py         copy fences from source; detect pipeline drift; mermaid/text label translation (§6.53, §6.62)
│   ├── fence_comments.py          fence ``//``/``#`` comments + ``text`` fence Cyrillic (§6.39, §6.59)
│   ├── prose_cyrillic.py          residual Cyrillic in EN prose/backticks (§6.45)
│   ├── ru_source_bugs.py          RU typo normalize (`--config-dir/opt`); anchor lines
│   ├── link_locale.py             URL locale mirror + link_locale QA (§6.34, §6.37)
│   ├── wikipedia_links.py         MediaWiki langlinks API (RU↔EN slugs, §6.37)
│   ├── cli_tokens.py              CLI token preservation (D.3)
│   └── heuristics.py              length ratio, cyrillic (+ fence comments), parity, anchors
├── harness/                       ✅ per-file + PR step runners (§6.66)
│   ├── state.py                   FileRunState — mutable per-file artifacts
│   ├── context.py                 HarnessContext — LLM, glossary, config
│   ├── steps.py                   Parse, Translate, LoadTarget, CriticFeedbackRetry, QA
│   ├── profiles.py                TRANSLATE_PROFILE / VERIFY_PROFILE
│   ├── runner.py                  FileHarness.run()
│   ├── pr_state.py                PRRunState — pair plans + results
│   ├── pr_context.py              PRHarnessContext
│   ├── pr_steps.py                PlanTranslate/VerifyPairs, ExecutePairPlans
│   ├── pr_profiles.py             TRANSLATE_PR_PROFILE / VERIFY_PR_PROFILE
│   ├── pr_runner.py               PRHarness.run()
│   ├── pair.py                    run_pair_plan → FileHarness
│   ├── cases.py                   YAML regression loader/runner
│   ├── render.py                  reinsert, finalize EN
│   └── critic_verdict.py          compute_critic_verdict
├── pipeline/                      ✅ COMPLETE (Phase F)
│   ├── translate_file.py          thin wrapper → harness (translate + verify)
│   ├── qa.py                      round-trip gate, compose_file_verdict
│   ├── pairs.py                   RU/EN md/includes + nav YAML pairing (§6.41)
│   ├── navigation_merge.py        scoped toc/redirect merge; EN-main indent (§6.36)
│   ├── completeness.py            source PR mirror coverage gate (§6.32)
│   ├── analyze.py                 pair plans — full re-translate (§6.30)
│   ├── orchestrator.py            thin wrapper → PRHarness (TRANSLATE_PR_PROFILE)
│   └── types.py                   result dataclasses
├── github/                        ✅ COMPLETE (Phase G)
│   ├── client.py                  GitHub REST (requests)
│   ├── git_ops.py                 local git diff / branch / commit / push
│   ├── pr.py                      PR context; load_pair / load_verify_pair (§6.31)
│   ├── workflow.py                run_doc_translate, run_doc_verify; _safe_post_issue_comment (§6.48)
│   └── errors.py                  typed GitHub errors
├── reporting/                     ✅ COMPLETE (Phase H)
│   ├── builder.py                 markdown reports; skipped tier + dedupe (§6.56–§6.57)
│   ├── heuristic_messages.py      Russian labels for heuristic warnings (§6.40)
│   └── locations.py               segment line links, excerpt sanity, heuristic dedup (§6.56)
├── version.py                     action_release_label() for report footer
├── config/                        ✅ COMPLETE
│   ├── default.yaml               packaged defaults
│   └── loader.py                  Pydantic schema + YAML + env override
├── cli.py                         ✅ Phase I — Typer CLI + __main__.py
└── prompts/                       ✅ v1 templates + glossary (packaged)
```

Legend: ✅ done · ⏳ pending · 🟡 partial.

Navigation YAML merge runs in `github/workflow.py` after per-file markdown
translation (`navigation_merge.run_navigation_merges`).

### 4.2. Files outside the package

- **GitHub Action (repo root):** `action.yml` (composite), `action-docker.sh`
  (build + GHCR fallback + `docker run`), `Dockerfile`, `entrypoint.sh` — see
  **08-operations** §19.4, **03-design-decisions** §6.49.
- `.github/workflows/docker-publish.yml` — optional GHCR publish (`workflow_dispatch`).
- `examples/ydb-github-doc-*.yml` — reference workflows for `ydb-platform/ydb`.
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
- `kind: SegmentKind` — `front_matter`, paragraph, heading, list_item,
  table_header_cell, table_body_cell, blockquote_paragraph, tab_title,
  term_definition.
- `path: list[str]` — breadcrumbs for LLM context, e.g.
  `["note:info", "table:header:col1"]` or `["front_matter:title"]`.
- `text: str` — markdown with `⟦K{n}⟧` placeholders (plain prose for front matter).
- `placeholders: list[ProtectedInline]` — what each marker means.
- `ast_path: list[int | str]` — mixed-step path to navigate AST when
  re-inserting. Strings are typed markers (`"header"`, `"row"`, `"title"`).

### 5.4. Placeholder prefixes (LLM contract)

| Prefix | Meaning | Example |
|---|---|---|
| `C` | inline code | `` `--yaml` `` → `⟦C1⟧` |
| `U` | link URL only (anchor text is translated) | `[docs](http://x)` → `[docs](⟦U1⟧)` |
| `S` | image src only (alt text is translated) | `![alt](img.png)` → `![alt](⟦S1⟧)` |
| `H` | inline html | `<br/>` → `⟦H1⟧` |
| `V` | YFM variable | `{{ ydb-short-name }}` → `⟦V1⟧` (prose only — not in `](...)`) |
| `T` | term ref | `[*cluster]` → `⟦T1⟧` |

**Counter is global per segment** (including inside `**bold**` / `*em*`).
Two links in one paragraph get `⟦U1⟧` and `⟦U2⟧`, never the same index.
Was a bug discovered in B.2, fixed by sharing `_ProtectState` across recursion.

After reinsert in `pipeline/translate_file.py`:

1. `localize_links_in_document` — AST walk: `mirror_link_href` for `/docs/ru/`,
   Wikipedia langlinks, Yandex/K8s path swaps (§6.37).
2. `_finalize_en_target` (§6.28): `enforce_source_fenced_blocks` →
   `localize_links_in_text` (regex safety net for Wikipedia URLs in raw markdown) →
   `postprocess_en_target_markdown` — homoglyphs, `<строка>`→`<string>` in fences,
   MD031 via `markdown_layout.py`.
3. **Renderer** (`markdown_renderer._join_blocks`) — `\n\n` between `fenced_code` and
   adjacent blocks; extra gap between tight list items when a fence precedes prose.

`llm/usage.py` — `UsageTracker.metrics_since(record_start)` for per-file token/cost
slices; `MODEL_PRICE_RUB_PER_1K` (§6.38). `reporting/builder.py` — cost block in
**all** report paths, including 🟢 «открытых замечаний нет» (§6.38).

[← Memory Bank index](../../MEMORY_BANK.md)
