# Memory Bank — Design decisions

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

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
would reuse `⟦U1⟧` and collisions in the restore map would silently swap
links.

### 6.12. Split link protection (anchor vs URL)
Whole-link `⟦L⟧` placeholders forced the model to keep Russian anchor text
and reinsert copied the entire RU `InlineLink`. Links now serialize as
`[protected anchor](⟦U{n}⟧)` with an href-only template in the placeholder map;
`reinsert` restores the original href from the `⟦U⟧` template; `mirror_link_href` runs only in `translate_file` via `localize_links_in_document`.

List/table HTML scaffolding (`<br/>`, `<ul>`, `<li>`, …) is **not** wrapped in
`⟦H⟧` so dense table cells stay translatable. `placeholder_repair` restores
`⟦V⟧`/`⟦C⟧`/`⟦U⟧` when the model emits `{{ var }}`, backticks, or bare URLs.

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

### 6.16. Why the Memory Bank is so verbose

This project is co-developed by the human owner and an AI assistant across many
chat sessions. Context loss between sessions is a real risk. The Memory Bank is
the canonical handover document. Verbosity is intentional.

Since post-D.2 it is split into [`docs/memory-bank/`](../../MEMORY_BANK.md)
parts; [`MEMORY_BANK.md`](../../MEMORY_BANK.md) at the repo root is the index.

### 6.17. TOC and redirect YAML — strict PR scope (not whole-file rewrite)

Diplodoc navigation files (`toc.yaml`, `toc*.yaml`, redirect/preservation YAML)
must **not** be fully re-translated on every PR. Only entries **changed in the
source PR** may be updated in the EN mirror.

**Problem:** A naive "translate the whole YAML" pass can (a) add EN menu items
for RU-only pages outside the PR, (b) drop EN-only legacy entries, or (c)
re-translate unchanged labels and drift from EN-main wording.

**Scope detection (RU base vs RU PR head):**

| File kind | Scope key | In scope when |
|---|---|---|
| TOC | `href` | New item, or existing item whose Russian `name` changed |
| Redirects | `from` | New entry, or existing entry whose `to` target changed |

Implementation: `navigation/toc.py` (`toc_translate_scope`, `merge_en_toc_yaml`,
`validate_toc_merge`) and `navigation/redirects.py` (same pattern).

**Merge rules (both kinds):**

1. **Unchanged key, already in EN-main** → keep EN block verbatim (no LLM).
2. **Key in scope** → take structure/value from RU PR; translate label (`name`)
   or copy `to` (redirects are usually language-neutral).
3. **RU-only key outside scope** → **skip** (do not invent EN entries).
4. **RU removed key** → omit from output (mirror RU PR structure).
5. **EN-only legacy key not in RU PR** → append unchanged at end.

This is **stricter than v1** (`main:toc_yaml.py`), which used `new_hrefs` (basenames
of newly translated `.md` files). v2 adds diff-based scope so **title-only**
changes on existing pages are picked up even when the `.md` basename was already
known. Orchestrator (Phase F) should union: `new_hrefs` ∪ `toc_translate_scope()`.

**Phase E hook:** `validate_toc_merge` / `validate_redirect_merge` flag
`unexpected_*`, `missing_*`, and `scope_not_applied` for the report.

**Phase F/G (workflow glue — TBD):** After per-file `.md` translation, if PR
touches `toc*.yaml` or redirect YAML, run scoped merge against EN-main + RU PR
head; write result to the paired EN path. Do **not** run merge for navigation
files outside the PR diff.

> **Status (2025-05):** merge/validate **APIs are implemented and tested**
> (`merge_en_toc_yaml`, `merge_en_redirects_yaml`, `validate_*`,
> `validate_navigation_merge_warnings`). **Not yet wired** into
> `pipeline/orchestrator.py` or `github/workflow.py` — markdown-only today.

Tests: `tests/unit/test_navigation_toc.py`, `test_navigation_redirects.py`,
`test_navigation_paths.py`, `test_validation_heuristics.py`.

### 6.18. Translation branch always on upstream (fork PRs)

**Problem:** Pushing `ydbdoc-review/pr-N` to the contributor fork (PR head repo)
requires write access to someone else's fork. GitHub Actions `GITHUB_TOKEN` only
has write on the upstream repo (`ydb-platform/ydb`), so fork pushes fail with
`permission denied`.

**Decision:**

1. **Translate** from the source PR diff / checkout (fork head content in CI).
2. **Create branch** on upstream only — never on the contributor fork.
3. **Branch from** `translation_branch_base(ctx)`:
   - fork PR → upstream `base_ref` (`main`);
   - same-repo → upstream source head branch.
4. **Push** to upstream; **open translation PR** with `base=translation_pr_base(ctx)`
   (same ref as branch start for fork PRs: merge translation into `main`).

Do not base the translation branch on the fork head: that replays foreign commits
and GitHub may reject push (`workflows` scope / permission errors).

Helpers: `translation_branch_base`, `translation_pr_base`, `is_fork_head` in
`github/pr.py`. See **07-pipeline** §16.3.

### 6.19. Batched critic (not whole-file)

**Problem:** Whole-file critic on large CLI docs (~600 lines, 150+ segments)
sends ~74k chars in one prompt and often needs a huge JSON response. With
`max_tokens=8000` the model hits `finish_reason=length` → empty/truncated JSON →
fallback with no issues.

**Decision:** Critic and verify use the **same segment chunker** as the
translator (`chunk_segments`, budget `translation.segments_per_batch_chars`).
Each batch prompt contains only `{id, kind, path, source_text, translated_text}`
for segments in that batch — not full file bodies. Batch results are merged
(`merge_critic_responses`).

Templates: `prompts/v1/critic_batch.md`, `verify_batch.md`. Legacy whole-file
templates (`critic.md`, `verify.md`) remain for reference but are not used in
the pipeline.

### 6.20. EN postprocess after render (homoglyphs + fence placeholders)

**Problem (PR #42380):** RU docs use `<строка>` inside shell examples; the model
copies it into EN. Cyrillic homoglyphs in YAML comments (`#FQDN ВМ`) slip through.
Cyrillic-in-EN heuristic skips fenced bodies, so `<строка>` was not flagged.

**Decision:** `postprocess_en_target_markdown` in `validation/homoglyphs.py` runs
on the full rendered EN string in `translate_file._render_with_translations`:

1. **Line homoglyphs** — on ASCII-heavy config lines (`#FQDN`, `host:`, …),
   map look-alike Cyrillic letters to Latin (`В`→`V`, `М`→`M`, …).
2. **Fence angle placeholders** — inside fenced code blocks only, map known RU
   words in `<…>` to EN (`<строка>`→`<string>`, `<значение>`→`<value>`, …).

Does not alter Russian prose or segment-level placeholder validation.

### 6.22. Fenced code is never sent to the translator

**Fact:** `segmentation/extractor.py` does **not** emit segments for `FencedCode` /
`IndentedCode` — only prose, headings, tables, tab titles, etc.

**Implication:** EN fenced bodies are copied from the RU AST at render time, not
from the LLM. If EN fences differ from RU, either (1) postprocess corrupted them
(now prevented), or (2) **RU SOURCE on the PR branch** already differed (e.g.
PR #40070 had `--config-dir/opt` and shortened `ca.crt` paths before translate).

**Pipeline guards (v0.1.0+):**

1. `normalize_ru_source_for_translation` — fix known RU typos (`--config-dir/opt`)
   on the RU string **before** parse/translate.
2. `enforce_source_fenced_blocks` — after render/postprocess, copy every code block
   body from source onto the target AST and re-render.
3. Heuristics: `fence_body_copy`, `fence_path_stripped`, `missing_anchor`,
   `detect_ru_source_bugs` (report fixes needed in **RU SOURCE**).

Allowed change inside a fence: RU→EN angle placeholders (`<строка>`→`<string>`)
via `fix_russian_angle_placeholders_in_en_fences` only.

### 6.23. Merge recommendation vs file verdict

**Problem:** Critic could return `verdict=warnings` with `issues=[]` after
auto-fixes; report listed files as OK but header stayed 🟡.

**Decision:** `_compute_verdict` treats empty `issues` as `ok` unless verdict is
`blocked`. `_merge_recommendation` counts files with **open** report items
(`_file_has_open_issues`), not raw `warnings` verdict alone.

### 6.21. Placeholder roles (V in prose, U in link URL)

**Problem:** LLM may keep placeholder **order** (`⟦V1⟧` then `⟦U1⟧`) but swap
**roles** — e.g. `[login](⟦V1⟧)` and `[](../../auth#…)` with empty anchor
(vscode-plugin `s0077`).

**Decision:**

- `placeholder_roles_valid` (`validation/placeholder_roles.py`) — `⟦V⟧` may
  appear in `](⟦V⟧)` only if the source segment does; `⟦U⟧` must appear in a
  link destination iff the source does.
- `placeholder_repair._repair_swapped_variable_and_url` + `_move_variable_clause_before_link`
  fix the common swap before validation; repair-pass handles remaining cases.

Order-only checks (`markers.placeholders_match`) are necessary but not sufficient.

### 6.22. Fence parity: AST at file level, regex per segment

**Problem:** `fence_parity` on raw markdown counted every line starting with
`` ``` `` **inside** fenced block bodies → false positives (14 vs 20 on
`deployment-preparation.md` when AST had 14 blocks each).

**Decision:**

- **File heuristic** `check_fence_parity` — count `FencedCode` nodes via
  `parse_markdown` (`heuristics._count_fenced_code_blocks`).
- **Segment validation** — `count_fence_markers` on segment `text` only (regex);
  catches model-added fences inside a translatable paragraph; triggers repair-pass.

Standalone `fenced_code` blocks are **not** segments (extractor skips them); they
round-trip from the source AST unchanged.

### 6.23. Merged source PR branch base

If the source PR is **merged** (`ctx.merged`), `translation_branch_base` uses
upstream `base_ref` (e.g. `main`), not the deleted head branch — same rule as
fork PRs. See `github/pr.py` (`PullRequestContext.merged`).

### 6.24. MD031 blanks around fences (tight lists + render)

**Problem (PR #42404):** markdownlint `MD031` / `blanks-around-fences` on EN
`deployment-configuration-v1.md` and `v2.md` — closing `` ``` `` immediately
followed by `- Section …` or `4. Set account …` with no blank line.

**Cause:** RU source has a blank line (e.g. after `` ``` `` before the next list
item). Parser marks the list **tight**; `render_markdown` joined list items with
no extra `\n` when `tight=True`, and joined `fenced_code` to the next block with
only a single `\n`.

**Decision:**

1. **`_join_blocks`** in `markdown_renderer.py` — `\n\n` between adjacent blocks
   when either is `fenced_code` / `indented_code`; between tight list items when
   the previous item ends with a fence and the next begins with prose.
2. **`fix_blanks_around_fences`** in `validation/markdown_layout.py` — line-based
   safety net in `postprocess_en_target_markdown` for already-rendered EN text.

**Tests:** `tests/unit/test_markdown_layout.py` (MD031 regression patterns from
#42404).

### 6.25. Critic / verify verdict normalization

**Problem:** Yandex models sometimes return non-schema `verdict` values (`needs_fix`,
`issues`, `issues_found`) → Pydantic parse fails → batch treated as empty warnings
(CI log noise, lost QA for that batch).

**Decision:** `normalize_critic_verdict_value` + alias map in `parse_critic_response`
before `CriticResponse` validation. Prompt `verify_batch.md` lists allowed literals
(same as `critic_batch.md`).

### 6.26. `doc_verify` segment alignment (no RU fallback)

**Problem:** On `enable_translate=False`, a failed `_align_translations` used to
fall back to `{seg.id: seg.text}` (Russian) → critic reported mass `(untranslated)`
on a structurally valid EN file.

**Decision:** Set `segment_alignment_error`, skip critic, `verdict=blocked`. Report
shows `(alignment)` under the file. Repair commit still only applies when critic
produced writable `target_text` changes.

### 6.27. Report checkout ref

Full reports include `Checkout: \`<short-sha>\`` from `git_head_sha(repo_path)` so
`doc_translate` vs `doc_verify` comments can be tied to the exact tree QA ran on.

---

---

[← Memory Bank index](../../MEMORY_BANK.md)
