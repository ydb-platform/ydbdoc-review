# Memory Bank — Design decisions

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

---

## 6. Key design decisions and trade-offs

> **Navigation / TOC:** §6.71–§6.90 describe historical supplement-chain fixes.
> Current behavior is **09-navigation-scope** §22 (Phase J, `d68812f`). See §6.91.

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

> **Status (2026-06):** wired in `github/workflow.py` via
> `pipeline/navigation_merge.py` (`run_navigation_merges`) after markdown
> translation. `build_navigation_pairs` detects changed RU `toc*.yaml` /
> redirect YAML; `completeness_gaps` (§6.32) blocks merge if any source PR
> mirror is missing from the commit.

Tests: `tests/unit/test_navigation_toc.py`, `test_navigation_redirects.py`,
`test_navigation_paths.py`, `test_validation_heuristics.py`.

**Inline TOC format (§6.33):** ydb `toc*.yaml` uses one-line items
`- { name: …, href: …, when: … }`. `parse_toc_items` must handle both this
and block `- name:` / `href:` layout. Also supports include-only lines
`- include: { mode: link, path: … }` (§6.84–§6.85) and indented list entries
under ``items:`` with deeper ``href:`` indent (§6.86). Empty merge (parser miss
or absent-EN scoped merge bug) is flagged `empty_toc` + `scope_not_applied` →
navigation verdict **blocked** → report 🔴.

### 6.38. Token usage and cost reporting (₽ per 1K tokens)

**Problems:**

1. Cost showed `~$0.00X` — price table used **USD per 1M** while Yandex AI Studio
   bills in **₽ per 1000 tokens** (sync mode, incl. VAT; see
   [Habr overview](https://habr.com/ru/articles/1030524/)).
2. Translate/repair `client.chat(model=…)` did not pass `role="translate"` →
   per-role token lines were empty in reports.
3. `FileTranslationResult.from_usage` stored **cumulative** tracker totals per
   file → misleading per-file aggregation fallback.
4. All-green reports (`По всем файлам открытых замечаний нет`) returned early
   **without** the «Стоимость и токены» block (PR #42745); source PR summary
   still showed cost.

**Decision:**

- `llm/usage.py`: `MODEL_PRICE_RUB_PER_1K`; `estimate_cost_rub()` divides tokens
  by **1000** (not 1_000_000). `estimate_cost_usd()` kept as alias returning RUB.
- `translator.py` / `repair.py`: `role="translate"` with explicit `model=` for
  usage tagging; `client.chat` allows both for tagging.
- `translate_file.py`: snapshot `usage_record_start`; `from_usage(record_start=…)`.
- `reporting/builder.py`: `_format_cost_rub()`; «Токены (всего)»; usage section
  appended on the all-green early-return path too.

Example (PR #42414, 3 files): ~14k in / ~8.5k out → **~₽10**.

### 6.37. Wikipedia links — deterministic langlink resolution

**Problem:** PR #42743–#42744 — LLM left `en.wikipedia.org/wiki/Копирование_при_записи`;
`mirror_link_href` only swapped host. MediaWiki API returned **403** without
`User-Agent` ([T400119](https://phabricator.wikimedia.org/T400119)) → silent
lookup failure in CI.

**Decision:** `validation/wikipedia_links.py`:

- `WikipediaResolver` calls `{lang}.wikipedia.org/w/api.php?action=query&prop=langlinks`
  with required `User-Agent: ydbdoc-review/0.1 (…)`.
- `resolve_wikipedia_href` — Cyrillic slug on `en.wikipedia.org` → lookup from
  `ru` article title; RU↔EN bidirectional via `target_lang`.
- Wired in `mirror_link_href` (AST) and `localize_links_in_text` (regex on final
  markdown in `_finalize_en_target`, §6.28).

QA `check_link_locale_in_en` still flags unresolved bad pairs (blocking). Success:
PR #42745 — `Copy-on-write` slug, 🟢 merge.

### 6.36. Inline TOC indentation preserved from EN-main

**Problem:** PR #42726 — merge appended RU inline lines as ``- {`` while EN-main
used `` - {``; Diplodoc failed with ``bad indentation of a sequence entry``.

**Decision:** ``merge_en_toc_yaml`` reads list-entry prefix from EN-main's first
inline item and normalizes every output line in ``_serialize_toc``.
``validate_toc_merge`` flags ``inconsistent_indent`` (blocking).

### 6.35. Navigation YAML in `doc_verify`

**Problem:** `doc_verify` only ran critic/heuristics on `.md`; `toc_i.yaml` never
appeared in verify reports even when present in the translation PR.

**Decision:** `build_verify_navigation_pairs` detects EN nav changes in the
translation PR diff and unions RU nav changes from the source PR (GitHub API).
`run_navigation_verifies` validates committed EN YAML against RU source PR head
(§6.31) using `validate_navigation_merge_warnings` — no LLM merge, read-only.
Results go to `navigation_results` and appear in the report like `doc_translate`.

### 6.34. External link locale (`link_locale`)

**Problem:** PR #42726 — host swap left Russian Wikipedia slugs on `en.wikipedia.org`;
QA initially reported 🟢.

**Decision:** Two layers:

1. **Fix (§6.37):** `mirror_link_href` / `localize_links_in_document` /
   `localize_links_in_text` — deterministic locale + Wikipedia langlinks.
2. **QA:** `check_link_locale_in_en` walks the EN AST and flags (blocking) if fix
   did not run or API failed:

   - RU-locale URLs (`ru.wikipedia.org`, `/docs/ru/`, …);
   - Cyrillic (incl. percent-encoded) paths on EN-locale hosts.

Wired in `run_file_heuristics_classified` for `target_lang=en`.

### 6.33. Inline Diplodoc TOC parsing + navigation blocking verdicts

**Problem:** PR #42725 — inline `toc_i.yaml` was parsed as zero items; merge
wrote `items:` only and ydbdoc-review still reported 🟢.

**Decision:** `navigation/toc.py` detects inline `- { name:, href: }` lines;
`validate_toc_merge` adds `empty_toc`; `scope_not_applied` (alias-aware, §6.74),
`unexpected_href`, `empty_toc` → `NavigationRunResult.verdict = blocked`;
`_merge_recommendation` treats nav `warnings` as 🟡 and nav `blocked` as 🔴.

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
2. `enforce_source_fenced_blocks` — after render, copy every code block body from
   source onto the target AST and re-render.
3. `translate_cyrillic_fence_comments_with_client` — **after** fence copy, batch-
   translate Cyrillic in ``//`` / ``#`` **line comments** only (§6.39).
4. Heuristics: `fence_body_copy`, `fence_path_stripped`, `missing_anchor`,
   `cyrillic_in_fence`, `detect_ru_source_bugs` (report fixes needed in **RU SOURCE**).

Allowed deterministic changes inside a fence (besides comment translate): RU→EN
angle placeholders (`<строка>`→`<string>`) via
`fix_russian_angle_placeholders_in_en_fences` in `postprocess_en_target_markdown`.

### 6.39. Cyrillic in fenced code comments (PR #42756 / debug-logs-otel)

**Problem:** PR #42756 — EN `debug-logs-otel.md` kept Russian ``//`` / ``#``
comments (e.g. `// 1. Настраиваем провайдер…`). ydbdoc-review reported 🟢.

**Root cause:** By design (§6.22) fenced bodies are copied verbatim from RU;
`check_cyrillic_in_en` **strips all fences** before scanning, so comment Cyrillic
was invisible to QA. Diplodoc build did not flag it either.

**Decision:**

1. **Finalize step** (`translate_file._finalize_en_target`): after
   `enforce_source_fenced_blocks`, run
   `translate_cyrillic_fence_comments_with_client` — one LLM JSON batch per file
   for ``//`` / ``#`` lines whose comment body contains Cyrillic. Code tokens,
   URLs, and identifiers stay unchanged.
2. **Heuristic** `check_cyrillic_in_en_fence_comments` → `cyrillic_in_fence: …`
   classified as **warnings** (not blocking). Runs on verify and translate QA.
   Prose Cyrillic remains **blocking** via `check_cyrillic_in_en`.

Implementation: `validation/fence_comments.py`. Tests:
`tests/unit/test_fence_comments.py`, `test_validation_heuristics.py`.

`check_fence_body_copy` treats comment-only ``//``/``#`` diffs (Cyrillic→EN) as
allowed — not pipeline corruption (PR #42762 false positives).

### 6.41. Locale-specific `_includes` in doc_translate scope

**Problem:** PR #40166 touched `ru/…/orm/_includes/toc-table.md`; translation PR #42766
had only 2 files — EN table on the ORM index page stayed without Kotlin Exposed.

**Root cause:** `is_docs_markdown` rejected **all** paths containing `/_includes/`.
That conflated two Diplodoc layouts:

| Path pattern | Role | Translate? |
|---|---|---|
| `ydb/docs/ru/…/_includes/*.md` ↔ `en/…/_includes/*.md` | Locale mirror (toc-table, auth, …) | **Yes** |
| `ydb/docs/_includes/…` (no `ru`/`en` prefix) | Repo-root neutral assets | No |
| `*.png`, `*.svg` under any `_includes/` | Images | No (not `.md`) |

**Decision:** `is_language_neutral_docs_path()` — neutral only when path is under
`docs/` but **not** under `docs/ru/` or `docs/en/`. `build_doc_pairs` and
`expected_en_mirrors` pick up locale includes automatically.

### 6.42. ``extra_toc_hrefs`` must not list locale ``_includes``

**Problem:** After §6.41, PR #42768 translated `orm/_includes/toc-table.md` but
`doc_translate` blocked merge: ``scope_not_applied: href 'toc-table.md' was in
translate scope but missing from EN toc``.

**Root cause:** `extra_toc_hrefs_from_md_targets()` unioned **every** translated
``.md`` basename into TOC scope. Include fragments (toc-table, auth snippets)
are not sidebar ``href``s — they must never appear in ``toc*.yaml``.

**Decision:** Skip paths containing ``/_includes/`` in
`extra_toc_hrefs_from_md_targets()`. TOC diff scope still comes from
`toc_translate_scope(ru_base, ru_pr)`; only standalone pages contribute
``new_hrefs``.

### 6.43. ``delete_en`` commits use ``git rm``, not ``git add``

**Problem:** PR #37955 renamed ``S3-enrichment.md`` → ``enrichment.md`` (RU delete +
add). ``doc_translate`` crashed on commit: ``pathspec '…/S3-enrichment.md' did not
match any files``.

**Root cause:** ``delete_en`` paths were appended to the same ``touched`` list as
writes; ``git_commit_paths`` always ran ``git add``. After
``prepare_translation_branch_on_base`` reset the tree to upstream ``main``, the EN
mirror was often already gone — ``git add`` fails with exit 128.

**Decision:** ``TouchedPaths(written, deleted)`` in ``workflow.py``.
``prepare_translation_branch_on_base`` unlinks ``deleted_paths`` on the new base;
``git_commit_paths`` runs ``git rm --ignore-unmatch`` for deletes, then ``git add``
for writes. Idempotent when EN mirror is already absent (merged/rename PRs).

### 6.44. Fork PR navigation baselines read upstream EN toc

**Problem:** PR #42884 (source #37955, fork, RU-only) collapsed ``toc_i.yaml`` to a
single ``enrichment.md`` item and blocked on ``index.md`` / ``topics.md`` in scope.

**Root cause:**

1. ``en_main`` was read at ``merge-base(origin/main, fork_HEAD)`` on the fork
   checkout — EN navigation files are often **absent** there. Scoped merge kept
   only in-scope hrefs.
2. ``extra_toc_hrefs_from_md_targets`` unioned every translated ``.md`` basename
   into **every** toc pair (``topics.md`` from recipes/, ``index.md`` page file).

**Decision (updated §6.111):** ``_read_navigation_baselines()`` — RU at
merge-base; **EN always from** ``merge_base_with`` (upstream ``main``), with
fallback to merge-base EN only when the file is still absent on main.
``extra_toc_hrefs_for_pair()`` intersects translated basenames with hrefs in
that RU PR toc before scope union.

### 6.45. Residual Cyrillic in EN prose and inline backticks (PR #43018 / topic.md)

**Problem:** PR #43018 — EN ``topic.md`` kept Russian inline terms
(`` `смещением` ``, `` `топик`, `источник` ``) inside otherwise English prose.
Critic returned ``ok``; ``check_cyrillic_in_en`` blocked the file (48 Cyrillic chars).

**Root cause:**

1. Translator/critic treat inline `` `…` `` as identifiers; LLM copied RU terms
   from bilingual RU patterns (`` `смещением` (offset) ``).
2. §6.39 fence-comment pass does not touch prose outside fences.
3. Homoglyph postprocess (§6.28) only fixes look-alike letters on ASCII-heavy
   config lines — not prose Cyrillic.
4. ``check_cyrillic_in_en`` detects but does not repair.

**Decision:**

1. **Finalize step** (`translate_file._finalize_en_target`): after fence-comment
   translate, run ``translate_cyrillic_prose_with_client`` — one LLM JSON batch
   per file for Cyrillic snippets in prose and inline backticks (fences excluded).
2. **Critic** prompt: flag residual Cyrillic in target prose/backticks as
   ``blocked``.
3. **Heuristic** ``check_cyrillic_in_en`` unchanged — still blocking when the
   prose pass fails or LLM leaves Cyrillic.

Implementation: ``validation/prose_cyrillic.py``. Tests:
``tests/unit/test_prose_cyrillic.py``.

### 6.46. YQL/SQL ``--`` comments in fenced blocks (PR #42886 / enrichment.md)

**Problem:** PR #42886 — EN ``enrichment.md`` kept Russian ``--`` comments in
`` ```yql `` blocks (10 lines). Report was 🟢 «можно мержить».

**Root cause:** §6.39 fence-comment pass and ``check_cyrillic_in_en_fence_comments``
only handled ``//`` and ``#``. YQL/SQL ``--`` lines were copied verbatim from RU
with no translate pass and no QA visibility (``check_cyrillic_in_en`` strips all
fences).

**Decision:** Extend ``validation/fence_comments.py`` — recognize line-start
``-- `` and trailing `` -- `` comments; same LLM batch translate + ``cyrillic_in_fence``
warning as §6.39.

Tests: ``tests/unit/test_fence_comments.py`` (YQL sample).

### 6.47. RU ``-rub`` asset suffix in EN image paths (PR #43034 / topic.md)

**Problem:** PR #43034 — EN ``topic.md`` referenced
``../../_assets/example-topic-design-rub.svg``. Diplodoc build failed:
``ENOENT: …/en/_assets/example-topic-design-rub.svg``. Report was 🟢.

**Root cause:**

1. Image ``src`` is copied from RU via ``⟦S{n}⟧`` placeholders (§6.22) — RU uses
   ``-rub.svg``, EN ``_assets/`` uses the same basename **without** ``-rub``.
2. ``mirror_link_href`` fixed HTTP locale URLs only, not relative asset paths.
3. ``check_link_locale_in_en`` scanned HTTP(S) hrefs only.

**Decision:** ``validation/link_locale.py``:

1. ``mirror_link_href`` — strip ``-rub`` before image extensions on relative paths
   when ``target_lang`` is EN.
2. ``check_link_locale_in_en`` — flag ``link_locale: RU asset suffix in EN relative path``.

Tests: ``tests/unit/test_link_locale.py``.

### 6.40. Human-readable heuristic messages in PR reports

**Problem:** Reports showed raw codes (`fence_body_copy: block 2…`,
`эвристика (файл)`), unclear to doc authors.

**Decision:** `reporting/heuristic_messages.py` — `humanize_heuristic()` and
`heuristic_location_label()` wired in `reporting/builder.py` for file and nav
warnings. Internal machine strings unchanged in `FileTranslationResult`; only
display layer translates them.

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

### 6.32. Source PR completeness gate (md + navigation YAML)

**Problem:** `doc_translate` could report 🟢 while omitting changed RU files
(e.g. `toc_i.yaml` filtered out by markdown-only pairing).

**Decision:** After markdown + navigation merge, `completeness_gaps` compares
`expected_en_mirrors(source PR diff)` with committed EN paths. Any missing
mirror → `completeness_gaps` on `PRTranslationResult` → 🔴 in report and commit
message still lists only what was written.

**Tests:** `tests/unit/test_completeness.py`, `test_navigation_pairs.py`.

### 6.31. `doc_verify` RU from source PR head (not translation branch)

**Problem:** Translation branches commit **EN only**; RU on disk is the branch
base (often current `main`). After the source PR merges, `main` RU can grow (e.g.
111 segments) while `doc_translate` used **source PR head** RU (e.g. 90).
`doc_verify` then compared `main` RU vs translation EN → false 🔴 alignment
(111 vs 90) while `doc_translate` reported 🟢 (90 vs 90 in-memory).

**Decision:** `load_verify_pair_contents` loads **EN** from the translation PR
checkout and **RU** via GitHub API at the **source PR head** commit (same tree as
`doc_translate` checkout). `source_pr_content_ref` resolves fork head repo when
needed.

**Tests:** `tests/unit/test_github_pr_verify.py`, updated `test_github_workflow.py`.

### 6.30. Full re-translate from PR source (no incremental EN patch)

**Problem:** Legacy EN on `main` could have fewer segments/fences than current RU
(e.g. 90 vs 110). `doc_translate` updated wording inside the old EN skeleton;
`doc_verify` then reported `segment count mismatch`. LLM pre-analyze could also
choose `critic_only` when both sides looked «semantically aligned», skipping a
full render from the source AST.

**Decision:**

1. **`doc_translate` always full re-translate:** read source text from the PR
   checkout, parse → translate all segments → render target from the **source AST**.
   Commit overwrites the mirror file; existing target text is never merged or patched.
2. **Source language** = the side authors edited in the PR (merge-base diff):
   - RU changed, EN unchanged → `translate_to_en` from RU when RU text exists.
   - EN changed, RU unchanged → `translate_to_ru` from EN.
   - **Both changed** → `skip` (§6.76) — bilingual PR; do not overwrite author's EN.
3. **No LLM analyze for action selection** in CI (`plan_pairs`, `use_analyze_llm=False`).
   `critic_only` remains only for **`doc_verify`** (`enable_translate=False`).
4. Pair with **`gate_round_trip`** (§6.29) blocks merge when render does not preserve
   segment parity.

**Tests:** `tests/unit/test_pipeline_analyze.py` (both-changed → skip §6.76);
orchestrator + workflow pass `use_analyze_llm=False`.

### 6.29. Unified QA (doc_translate ≡ doc_verify)

**Problem:** `doc_translate` ran critic on in-memory translations; `doc_verify`
re-parsed EN and required `_align_translations`. Identical EN could be 🟡 then 🔴.

**Decision (`pipeline/qa.py`, `translate_file.py`):**

1. **Always** `normalize_ru_source_for_translation` before parse (both modes).
2. After render/finalize (translate) or reading EN (verify): **`gate_round_trip`**
   — re-parse EN, segment count must match RU; else `segment_alignment_error` + 🔴.
3. Critic uses translations from successful round-trip only.
4. **Classified heuristics:** `blocking` | `warnings` | `info` (`ru_source` → info only).
5. **`compose_file_verdict`** — one rule for merge recommendation.
6. `fence_content_matches_source` allows homoglyph + angle-placeholder deltas;
   `check_absolute_paths_in_fences` skips when block counts differ (no `zip` crash).

Report: blocking/warnings in «Что исправить»; `heuristic_info` in «Справка (не блокирует merge EN)».

### 6.48. Translation report before source PR comment (PR #43151)

**Problem:** [PR #43151](https://github.com/ydb-platform/ydb/pull/43151) — translation
commit and branch were pushed, but the QA report comment was missing. CI run
`27288680755` failed with `HTTP 401` on
`POST …/issues/42789/comments` (short summary on the **source** fork PR).

**Root cause:** `run_doc_translate` posted the source-PR comment **before** the
translation-PR QA report. `post_issue_comment` raised `GitHubAPIError` → CLI
exited with code 1 → translation report never posted. Push / `create_pull` /
`add_issue_labels` had already succeeded on the translation PR.

**Decision:**

1. Post the **translation PR** full report (`build_full_report`) **first**.
2. Post the **source PR** short summary (`build_source_pr_comment`) second.
3. Wrap both in `_safe_post_issue_comment` — log `warning`, return `None`; do
   **not** fail the job when a comment POST returns 4xx (fork source PRs may
   intermittently get 401 even when translation-PR API calls work).

Same helper for `doc_verify` report posting.

**Tests:** `tests/unit/test_github_workflow.py`
(`test_run_doc_translate_source_comment_failure_still_posts_report`).

**Note:** unrelated to [ydb #43126](https://github.com/ydb-platform/ydb/pull/43126) (CI
cascade / `YDBOT_TOKEN` for `ok-to-test` + `rebuild_docs`). After #43126,
`trigger-translation-ci` runs only when `ydbdoc-review` job **succeeds** — so
`_safe_post_issue_comment` (§6.48) also keeps downstream CI labels working when
source-PR comment fails.

### 6.50. `doc_verify` fork fallback: open separate fixup PR ([ydb #41451](https://github.com/ydb-platform/ydb/pull/41451))

**Problem (Jun 2026):** running `doc_verify` on a contributor PR whose head is on a
fork (e.g. `AlejandroMokhovani/ydb`, `YDBDOCS-943-...` branch) failed with
`git push ... permission denied`. CI `GITHUB_TOKEN` only has `contents:write` on
the upstream repo, never on contributor forks — and GitHub forbids `GITHUB_TOKEN`
pushes to forks regardless of `maintainerCanModify`.

Historically `verify_push_remote_url` returned the head repo URL (works for
translation PRs that live on upstream as `ydbdoc-review/pr-N`). For fork-head PRs
the push always rejects.

**Decision:** detect `is_fork_head(ctx)` up front. When True:

1. Reset a fresh branch `ydbdoc-review/verify-{source_pr or pr_number}` off
   upstream `ctx.base_ref` via `prepare_translation_branch_on_base` — same helper
   `doc_translate` uses.
2. Commit critic fixes and push that branch to upstream (`GITHUB_TOKEN` has
   `contents:write` there).
3. Open a fixup PR via `gh.create_pull` targeting `ctx.base_ref` (typically
   `main`). Title: `Critic fixes for #{pr_number}`. Body: explains the fork
   constraint and points back at the source PR (`build_verify_fixup_pr_body`).
4. Post a short link comment on the source PR
   (`build_verify_fixup_source_comment`) — through `_safe_post_issue_comment`
   because fork source PRs sometimes return HTTP 401 (§6.48).

Non-fork case (translation PR on upstream) originally used direct-push; see §6.75.
No critic fixes (`touched` empty) → no extra commit / fixup PR, only the QA report.

> **Superseded:** §6.64 — author/fork/manual PRs use fixup branch/PR only.
> §6.75 — translation PR ``ydbdoc-review/pr-{N}`` pushes critic fixes inline on
> ``ctx.head_ref`` (no ``ydbdoc-review/verify-*``).

Multiple `doc_verify` runs on the same source PR: the local branch is reset off
base by `prepare_translation_branch_on_base`, but the **remote** ref still carries
the previous run's commits, so a plain `git push HEAD:refs/heads/<branch>` is
rejected as non-fast-forward. Before pushing, `run_doc_verify` calls
`gh.delete_branch(owner, repo, fixup_branch)` to drop the stale ref (and let
GitHub auto-close the old fixup PR). The push then creates the ref fresh and
`gh.create_pull` opens a new fixup PR — see §6.52.

**Config:** `cfg.paths.verify_fixup_branch_prefix = "ydbdoc-review/verify-"`.

**Implementation:** `src/ydbdoc_review/github/workflow.py:run_doc_verify`,
`src/ydbdoc_review/github/pr.py:verify_fixup_branch`,
`src/ydbdoc_review/reporting/builder.py:build_verify_fixup_pr_body`,
`build_verify_fixup_source_comment`. Tests:
`tests/unit/test_github_workflow.py:test_run_doc_verify_fork_head_opens_fixup_pr`.

### 6.49. GitHub Action: local Docker build + GHCR fallback

**Problem (Jun 2026):** `action.yml` with `image: Dockerfile` made every `doc_translate`
in ydb rebuild the image on the runner. GitHub-hosted runners intermittently failed
with `i/o timeout` pulling `python:3.12-slim` from `registry-1.docker.io`.

**Attempted fix (reverted pattern):** `image: docker://ghcr.io/.../v0.1.0` only +
auto-publish on every tag push — worked but forced **waiting for GHCR publish** on
each `git tag -f v0.1.0` bugfix.

**Decision (current):**

| Piece | Role |
|-------|------|
| `action.yml` | `composite` — runs `action-docker.sh` |
| `action-docker.sh` | 1) `docker build` from checked-out action ref; 2) on failure `docker pull ghcr.io/ydb-platform/ydbdoc-review:<GITHUB_ACTION_REF>` |
| `Dockerfile` | Base `public.ecr.aws/docker/library/python:3.12-slim` (Docker Hub mirror) |
| `entrypoint.sh` | Unchanged; container entrypoint |
| `.github/workflows/docker-publish.yml` | **Optional** GHCR publish — `workflow_dispatch` only |

**Release loop (bugfix):**

```bash
git tag -f v0.1.0 HEAD && git push -f origin v0.1.0
# re-add doc_translate in ydb — no GHCR wait
```

Run **Publish action image** manually when fallback image should match latest code
(e.g. after long period of Docker Hub outages). Fallback tag matches action ref
(`@v0.1.0` → `:v0.1.0`).

**Implementation:** repo root `action-docker.sh`, `action.yml`, `Dockerfile`;
details in **08-operations** §19.4.

### 6.28. EN finalize order: enforce fences, then postprocess

**Problem (PR #42548):** `postprocess_en_target_markdown` (homoglyphs, `<строка>`→`<string>`)
ran inside `_render_with_translations`, then `enforce_source_fenced_blocks` copied
verbatim RU fence bodies **over** those fixes → EN still had `#FQDN ВМ` and `<строка>`.

**Decision:** `_finalize_en_target` = `enforce_source_fenced_blocks` →
`localize_links_in_text` (Wikipedia + locale URLs, §6.37) →
`postprocess_en_target_markdown`. Homoglyphs and angle placeholders apply to the
final EN text, including list-indented fences.

**Heuristics:** `check_fence_body_copy` compares against `normalize_ru_source_for_translation`
(raw RU), not raw typo text — avoids false `fence_body_copy` when EN correctly has
`--config-dir /opt`. `ru_source` still warns on **raw** RU (author must fix source PR).
`detect_ru_source_bugs` message states «исправьте в RU PR». `_strip_fenced_blocks` in
cyrillic check allows leading whitespace before `` ``` `` (indented fences).

### 6.51. `doc_verify` render base = EN AST (preserve EN fence bodies, PR #43399)

**Problem ([ydb #41206](https://github.com/ydb-platform/ydb/pull/41206) → fixup
[ydb #43399](https://github.com/ydb-platform/ydb/pull/43399)):** `doc_verify` on
`streaming-query/checkpoints.md` produced English text where Mermaid fenced blocks
had Russian participant names:

```
participant Топик
participant Запрос v1
participant Запрос v2
```

The EN file already had correct `participant Topic` / `Query v1` / `Query v2`; the
critic should never touch fence bodies.

**Root cause:** `translate_file` in critic-only mode (`enable_translate=False`)
parsed the **RU** source into `source_doc`, ran the critic against the existing EN
text via `gate_round_trip`, applied critic fixes, then re-rendered using
`copy.deepcopy(source_doc)`. The RU AST carries the **RU** fenced code blocks
verbatim (RU author of `checkpoints.md` had written `participant Топик` in his
Mermaid). `reinsert_segments` only updates inline-bearing segments — fence blocks
pass through untouched, so RU fence bodies ended up in the EN output. Then
`_finalize_en_target` made it worse by calling
`enforce_source_fenced_blocks(text, normalized_source_text=RU)`, which **explicitly**
copies fence content from the RU source. The bug only fired when the critic
returned at least one issue (otherwise `translated_text` stayed equal to
`existing_target_text`).

**Decision:** in `enable_translate=False` mode, the **EN existing text** is the
render base.

1. Parse `existing_target_text` once at the top of the verify branch →
   `render_base_doc` + `render_base_segments`.
2. `_render_with_translations(render_base_doc, render_base_segments, …)` —
   deepcopying the EN AST means fenced code blocks remain English.
3. Translations are still keyed by RU segment ids during the critic pass (the
   prompt sees RU `source_text` / EN `translated_text`); just before render they
   are re-keyed to EN segment ids by zipped position
   (`_remap_translations_by_position`). This is safe because `gate_round_trip`
   has already enforced `len(ru_segments) == len(en_segments)`.
4. Pass `existing_target_text` as the `normalized_source_text` argument to
   `_finalize_en_target` so `enforce_source_fenced_blocks` becomes effectively a
   no-op for fence bodies (EN fences match EN fences). Cyrillic-fence-comment
   translation and Cyrillic-prose translation still run — they're still useful
   in verify mode for catching residual RU text the original translation may
   have left behind.
5. If parsing the existing EN target fails or segment counts disagree, fall back
   to the source (RU) base — the verdict will be `blocked` on alignment error
   anyway, so the regression risk is bounded.

`doc_translate` is unchanged: render base stays the RU `source_doc` (target
doesn't exist yet, so there's nothing to preserve).

**Tests:** `tests/unit/test_translate_file.py::test_translate_file_verify_preserves_en_fence_bodies`
reproduces the original mermaid `participant Topic` corruption and proves the
fix preserves the EN fence body while still applying critic-suggested prose
fixes outside the fence.

**Tag note:** `v0.1.0` was force-moved to the fix commit; no schema or CLI
change.

### 6.52. `doc_verify` fork fallback: reset stale fixup branch before push

**Problem:** running `doc_verify` a second time on a contributor PR (fork head,
e.g. `YDBDOCS-XXX-...`) crashed at `git push`:

```
! [rejected] HEAD -> ydbdoc-review/verify-<N> (non-fast-forward)
```

The first run pushed the fixup branch and opened a fixup PR. The second run reset
the **local** branch off `ctx.base_ref` via `prepare_translation_branch_on_base`
and committed fresh critic fixes, but the **remote** ref still carried the
previous commit. A plain `git push HEAD:refs/heads/<branch>` is non-fast-forward
in that state, so the action failed before posting the QA report.

§6.50's earlier "branch is reused" claim was wrong — `prepare_translation_branch_on_base`
only resets locally; the remote ref still needed handling.

**Decision:** before the fixup push (§6.64 author/fork path only — not §6.75
translation inline push), drop the stale remote ref via
`gh.delete_branch(owner, repo, fixup_branch)`. The push then creates the ref
fresh. GitHub auto-closes any open PR whose head was the deleted ref, so
`gh.create_pull` opens a new fixup PR rather than reusing the old one — a small
amount of fixup-PR churn in exchange for an idempotent re-run path.

`delete_branch` returns False on 404/422 (ref already absent), so the first run
on a PR is a no-op delete and the code path is uniform.

Token use: `delete_branch` runs through the API client (`api_token` =
`GITHUB_TOKEN`), which in the production workflow grants `contents: write` on the
upstream repo — the same scope the push needs.

**Implementation:** `src/ydbdoc_review/github/client.py:GitHubClient.delete_branch`,
call site in `src/ydbdoc_review/github/workflow.py:run_doc_verify` (fork-fallback
branch, before `push_branch`).

**Tests:**

- `tests/unit/test_github_client.py::test_delete_branch_success` /
  `::test_delete_branch_missing` — 204 vs 422 contract.
- `tests/unit/test_github_workflow.py::test_run_doc_verify_fork_head_opens_fixup_pr` —
  asserts `delete_branch` is called with the fixup branch name on every run.
- `tests/unit/test_github_workflow.py::test_run_doc_verify_fork_head_resets_existing_fixup_branch` —
  simulates a stale remote ref (`delete_branch` returns True) and confirms the
  push then proceeds and a fresh fixup PR is opened.

**Tag note:** `v0.1.0` was force-moved to the fix commit; no schema or CLI
change.

### 6.53. Critic auto-fix regression guard + mermaid `fence_body_copy` ([ydb #41206](https://github.com/ydb-platform/ydb/pull/41206))

**Problem (Jun 2026, second `doc_verify` on #41206):** after §6.51 fixed EN fence
preservation, two issues remained:

1. **False 🔴 + harmful auto-fix:** critic flagged `streaming-query.md` segment
   `s0023` as «missing content» (Kafka/PostgreSQL sentence) even though the
   contributor's EN already contained it. The truncated `suggested_text` was
   auto-applied in fixup PR [#43438](https://github.com/ydb-platform/ydb/pull/43438)
   and **removed** the correct sentence from the committed output.
2. **False 🟡 `fence_body_copy`:** `checkpoints.md` Mermaid blocks with English
   `participant Topic` / `Query v1` were reported as «differs from RU» because
   the heuristic required byte-identical fence bodies. Label translation is
   expected in Mermaid diagrams.

**Decision:**

1. **`apply_critic_fixes` regression guard** (`translation/critic.py`):
   skip auto-apply when the issue reads like a missing-content complaint
   (`missing`, `omit`, `пропущ`, …) but `suggested_text` is **shorter** than the
   current segment translation, or when `suggested_text` ends with `…` / `...`
   (truncated LLM output). The issue stays in the report for human review; it is
   not written to disk.
2. **Mermaid-aware fence compare** (`validation/fence_integrity.py`):
   `_fence_diff_is_mermaid_label_translation` — same line count and structural
   skeleton (`participant *`, `*->>*`, `Note over *`, …) with Cyrillic/Latin
   labels allowed to differ. Wired into `fence_content_matches_source` so
   `check_fence_body_copy` stays quiet for translated diagrams.

**Tests:** `test_apply_critic_fixes_skips_missing_content_that_shortens`,
`test_apply_critic_fixes_skips_truncated_suggestion`,
`test_fence_content_allows_mermaid_label_translation`,
`test_fence_content_rejects_mermaid_structure_change`.

**Complements §6.51:** §6.51 stops RU fence bodies from replacing EN on re-render;
§6.53 stops critic auto-fix from deleting good prose and stops false fence warnings
on legitimately translated Mermaid.

### 6.54. Mermaid message/Note lines + ``⟦V⟧`` drift filter ([#41206](https://github.com/ydb-platform/ydb/pull/41206))

**Problem (third `doc_verify` on #41206, Jun 15):** report stayed 🟡 with:

1. **``fence_body_copy`` block 2** in `checkpoints.md` — §6.53 skeleton compare
   required identical token count in `Note over …:` / arrow message lines; EN
   `Events E, F arrive` vs RU `События E, F поступают в топик` failed.
2. **Critic ``placeholder corruption``** on `streaming-query.md` — human EN used
   `{{ ydb-short-name }}` 3× where RU segment model has 4× ``⟦V⟧``; meaning OK,
   segment gate flagged drift.

**Decision:**

1. **Mermaid line kinds** (`fence_integrity._mermaid_structure_line`):
   - `participant` / `participant * as *` — label only;
   - `Note over *:` — header structure only, prose after `:` ignored;
   - arrow lines (`->>`, `--x`, …) — compare prefix before message colon only.
2. **`variable_placeholder_drift_only`** (`validation/markers.py`) — non-``⟦V⟧``
   placeholders must match; ``⟦V⟧`` count may differ by ≤1.
3. **`drop_spurious_placeholder_issues`** (`validation/placeholder_drift.py`) —
   before `apply_critic_fixes` and after `run_verify`, drop critic issues whose
   only complaint is ``⟦V⟧`` drift; recompute verify verdict.

**Tests:** `test_fence_content_allows_mermaid_note_and_message_translation`,
`test_drop_spurious_placeholder_issues_streaming_query_style`,
`test_filter_critic_response_clears_verdict`.

---

### 6.55. Cross-language placeholder alignment ([ydb #40466](https://github.com/ydb-platform/ydb/pull/40466))

**Problem (Jun 17):** `doc_verify` on `columns.md` spammed the same
``placeholder mapping`` block on s0013 / s0014 every run, and the apply path
corrupted a *correct* EN translation. Root cause is that RU and EN segments
are parsed independently; each gets a fresh left-to-right placeholder
numbering inside its own language. For

- RU `…к таблице ⟦C1⟧ колонку ⟦C2⟧ с типом ⟦C3⟧` (C1=`episodes`, C2=`views`, C3=`Uint64`)
- EN `column ⟦C1⟧ data type ⟦C2⟧ to ⟦C3⟧ table` (C1=`views`, C2=`Uint64`, C3=`episodes`)

the same name means a *different* atom in each language. The critic LLM
never sees the atoms; it assumes ``⟦C1⟧`` is shared and reports
"placeholder order mismatch" on every legitimate word-order shift. It then
suggests `column ⟦C2⟧ … ⟦C3⟧ … ⟦C1⟧ table` to "restore" source order —
text which, when applied with the EN segment's placeholder map, substitutes
the wrong atoms in the wrong slots (`column Uint64 … episodes … views table`).

**Failed first attempt:** relaxing ``placeholders_match`` to compare a
*multiset* of placeholders (commit `b2c3f2e`) cleared the false positive in
`doc_translate` (LLM legitimately reorders, both sides share RU numbering,
multiset is safe). In `doc_verify` it removed the inadvertent safeguard:
critic reorders now passed validation and corrupted EN files via apply (fixup
PR #43698, `columns.md` lost the correct mapping). Half-fix `47583c2` added
``strict_placeholder_order`` in `apply_critic_fixes` for the verify path —
files stop getting corrupted, but the critic still spams the report on every
RU/EN word-order shift, which kills the system's usability as a gate.

**Decision (commit `641b53b`):** renumber EN target segments so each atom
that appears in both languages takes the source's name. New module
`segmentation/placeholder_align.py` exposes
``normalize_target_segments_to_source(source, target)`` and matches atoms by
identity:

| Atom kind        | Match key                                          |
|------------------|----------------------------------------------------|
| `InlineCode`     | `content` (code spans don't translate)             |
| `InlineVariable` | `name` (`{{ backend_name }}` etc.)                 |
| `InlineLink`/URL | `href` with `/ru/` or `/en/` prefix stripped       |
| `InlineImage`    | `src`                                              |
| `InlineHTML`     | `content`                                          |

Duplicate atoms are paired left-to-right (1st `episodes` in target → 1st
`episodes` in source). Target-only atoms (e.g., translator-added code) keep
their name when it doesn't clash, otherwise get a fresh non-clashing index
*per kind*. Renumbering uses a single regex pass so `⟦C1⟧↔⟦C2⟧` swaps don't
double-apply.

**Wired in:**

1. `align_translations_from_target` (`pipeline/qa.py`) — every `translations`
   dict returned to the critic and the apply path carries RU numbering.
2. `doc_verify` render base in `pipeline/translate_file.py` —
   `render_base_segments` are normalized before reinsertion, so
   `seg.placeholders` and `translation_text` share names and substitution
   finds the right atoms.

`doc_translate` is a no-op: the LLM already emits markers in RU numbering, so
`rename` is empty and the original target segment is returned unchanged.

**Invariants this gives:**

- Same ``⟦Xn⟧`` always refers to the same atom across RU and EN inside a
  pair — critic stops reporting reorderings as bugs.
- A *real* mistranslation (e.g., `Uint64` placed where `views` should be)
  still shows up: atom matching pairs `Uint64`↔`Uint64`, but the position is
  wrong relative to surrounding prose — the critic catches it honestly.
- `apply_critic_fixes` validation (multiset) and the strict-order guard in
  the verify path both keep working; with consistent numbering they rarely
  fire because the critic stops suggesting reorders.

**Tests:** new `tests/unit/test_placeholder_align.py` covers
`columns.md` s0013 reorder, no-op when numbering already matches, URL locale
normalization, YFM variable matching, duplicate-atom left-to-right pairing,
unmatched target rename, image matching by `src`, count-mismatch passthrough,
atomic swap renumbering. Existing critic regression
`test_apply_critic_fixes_strict_order_rejects_reorder` (commit `47583c2`)
remains as belt-and-suspenders.

**Why earlier "strict order" guard stays:** even with correct numbering, a
critic that hallucinates a reorder shouldn't be auto-applied in the verify
path — apply still runs through the EN AST and the cost of a bad apply is a
corrupted file. The cost of a skipped good fix is a noisy report.

### 6.56. doc_verify noise reduction ([ydb #40466](https://github.com/ydb-platform/ydb/pull/40466))

**Problem (Jun 17):** even with §6.55, `doc_verify` on #40466 still reported
~18 issues per run; most were pipeline noise (placeholder reorder after
correct translation, mirror URLs with different relative paths, broken
``📍 Искать`` excerpts, skipped critic fixes counted as open blockers).

**Decision:**

1. **URL mirror matching** (`placeholder_align._normalize_doc_href`) — pair
   RU/EN doc links by **basename** (strip ``../`` depth and fragment). Fixes
   false ``⟦U1⟧→⟦U2⟧`` when paths differ but target the same file
   (``mvcc.md``, ``create_table/index.md``).
2. **NULL atom equivalence** — ``InlineCode("NULL")`` matches ``null``
   case-insensitively for align keys.
3. **Cross-lang spurious filter** (`validation/placeholder_drift.py`,
   ``cross_lang_placeholder_drift_only`` in ``markers.py``) — drop critic
   placeholder issues when non-``⟦V⟧`` multiset matches and the comment is
   about order/reorder/mapping (extends §6.54 ``⟦V⟧``-only filter).
4. **Atom legend in critic batch** — ``segments_to_critic_batch_json`` adds
   ``atom_map`` per segment; ``critic_batch.md`` instructs the model not to
   flag word-order shifts when ``atom_map`` shows the same atoms under the
   same marker names.
5. **Segment mismatch diagnostics** (`pipeline/qa.describe_segment_alignment_mismatch`)
   — alignment errors name the first extra/mismatched segment instead of only
   ``437 vs 436``.
6. **Excerpt sanity** (`reporting/locations.excerpt_found_in_file`) — omit
   ``📍 Искать`` when the preview is broken (e.g. ``(e.g., )`` from wrong
   placeholder restore). ``doc_verify`` builds line maps/excerpts from
   ``render_base_segments`` (EN placeholders), not RU source placeholders.
7. **Report tiers** — ``critic_skipped`` no longer inflates the main issue
   list or 🔴 verdict; shown in a collapsed
   «Автоисправление не применено» block
   (``reporting.include_skipped_critic``, default ``true``).

**Tests:** extended ``test_placeholder_align.py``, ``test_placeholder_drift.py``,
``test_qa.py``, ``test_reporting_builder.py``.

### 6.57. doc_verify false-positive filters round 2 (#40466)

**Problem (Jun 17, post-§6.56):** rerun on #40466 still listed ~26 items;
~half were pipeline bugs — verify echoed ``critic_skipped`` in the main list,
``atom_map`` marker-id noise, Wikipedia locale false alarms, NULL literal
ping-pong in YFM tabs, critic hallucinations (``AUTO_PARTITIONING_*`` →
``⟦C1⟧``), and ``VACUUM`` vs ``⟦C1⟧`` equivalence.

**Decision:**

1. **Skipped ∩ unresolved dedupe** — ``exclude_skipped_issues`` in
   ``filter_critic_response`` (verify pass) and ``_remaining_critic_issues``
   (report builder) so the same apply-rejected item appears only in
   «Автоисправление не применено», not twice.
2. **Marker-id / atom_map noise** — extend cross-lang spurious filter to
   drop placeholder issues when the non-``⟦V⟧`` multiset matches *and* the
   comment is about order / atom_map / marker id (covers post-align ``⟦U2⟧
   not in atom_map``).
3. **Wikipedia locale** — drop locale complaints when multiset matches and
   the segment carries a Wikipedia link placeholder (``en.wikipedia`` vs
   ``ru.wikipedia`` is expected after ``localize_links``).
4. **NULL literal ping-pong** — drop NULL ↔ ``⟦C{n}⟧`` issues when both RU
   and EN segments reference NULL (literal or ``code:null`` atom).
5. **Code literal equivalence** — drop when critic flags bare SQL identifier
   vs ``⟦C{n}⟧`` but both sides carry the same code atom (e.g. ``VACUUM``).
6. **Hallucinated substitution** — drop when critic claims
   ``IDENTIFIER was replaced by ⟦C1⟧`` but EN text still contains the
   identifier and not the claimed placeholder.

**Tests:** ``test_placeholder_drift.py`` (§6.57 regressions),
``test_reporting_builder.py`` (skipped dedupe in main list).

**Release:** tag ``v0.1.0`` @ commit ``5293a77`` (Jun 17, 2026).

**Implementation notes:**

- ``critic_issue_dedupe_key`` — ``(segment_id, category, comment, suggested_text)``.
- ``filter_critic_response(..., skipped=critic_skipped)`` wired in
  ``pipeline/translate_file.py`` after ``run_verify``.
- ``reporting/builder._remaining_critic_issues`` also calls ``exclude_skipped_issues``
  (defence in depth).
- Skipped-only files (no open critic/heuristic/manual items) still render the
  collapsed «Автоисправление не применено» block even when ``verdict != ok``.

### 6.58. #40466 validation — human EN PR after §6.57 ([ydb #40466](https://github.com/ydb-platform/ydb/pull/40466))

**Context:** fork PR ``ayakivosklznak/ydb`` branch
``DOCSUP-129689-encoding-translation`` — five EN files translated by a human
while RU lives on ``main``. Canonical ``doc_verify`` stress test for
§6.55–§6.57.

**Run timeline (Jun 17, 2026):**

| Time (UTC) | Tag / commit | Open items | Notes |
|---|---|---|---|
| 12:40 | pre-§6.55 | many 🔴 | placeholder reorder noise on ``columns.md`` |
| 14:17 | ``798969a`` (§6.56) | ~26 | mostly pipeline noise; broken excerpts |
| 15:27 | ``5293a77`` (§6.57) | **1** | only real alignment blocker left |

**Latest report** ([comment 4732251498](https://github.com/ydb-platform/ydb/pull/40466#issuecomment-4732251498)):
checkout ``d8fa52d7a447`` (fixup branch ``ydbdoc-review/verify-40466``).

| File | Verdict | Notes |
|---|---|---|
| ``store.md`` | 🟢 | was 🔴 (placeholder / excerpt noise) |
| ``table.md`` | 🟢 | was 🔴 (Index link, AUTO_PARTITIONING hallucinations) |
| ``columns.md`` | 🟢 | was 🔴 (§6.55 reorder false positives) |
| ``create_table/index.md`` | 🟢 | was 🔴 (NULL ↔ placeholder ping-pong) |
| ``glossary.md`` | 🔴 | **real author issue** — see below |

**Remaining blocker (author, not pipeline):** ``glossary.md`` —
``segment count mismatch: source 437 vs target 436``; first diff at pair
index **30**: RU ``s0031`` (**paragraph**) vs EN ``s0031`` (**heading**).

Root cause: EN is missing RU content in the **Storage group** block:

1. **Paragraph** after the “Distributed storage typically manages…” sentence —
   RU (``main``): static/dynamic groups are **physical** (data on
   [VDisk](#vdisk)s). EN jumps straight to ``#### Static group``.
2. **Section** ``#### Virtual storage group {#virtual-storage-groups}`` — present
   in RU ``main``, absent in EN (heading + definition paragraph).

Until EN structure matches RU here, round-trip alignment fails → critic is
skipped for the whole file → 🔴 is correct.

**Pipeline vs author classification (post-§6.57):**

- **Fixed by pipeline:** duplicate skipped/unresolved in report; ``atom_map``
  marker-id noise; Wikipedia locale false alarms; NULL literal ping-pong;
  ``VACUUM`` vs ``⟦C{n}⟧``; critic ``AUTO_PARTITIONING_* → ⟦C1⟧`` hallucinations.
- **Still author:** ``glossary.md`` structural gap (above). Optional stylistic
  nits (e.g. ``e.g.,`` in ``store.md``) no longer block merge once glossary aligns.

**Cost reference:** latest run ~145k / 63k critic tokens, ~₽98 (``deepseek-v32``).

### 6.59. #43365 auto-translate fixes — OTel metrics docs ([ydb #43365](https://github.com/ydb-platform/ydb/pull/43365))

**Context:** auto-translate from source PR [#41691](https://github.com/ydb-platform/ydb/pull/41691),
branch ``ydbdoc-review/pr-41691``. Last ``doc_translate`` @ ``5293a77`` (§6.57) left
🔴 on ``debug-otel-metrics.md`` (tab C++ ``s0109``) and 🟡 verify with critic fix not
applied; navigation and diagram text also incomplete.

**Root causes (pipeline, not author):**

| Symptom | Cause | Fix |
|---|---|---|
| ``s0109`` placeholder issue skipped | §6.57 filter treated identical ⟦C⟧ sequence + “order/mapping” comment as spurious reorder noise | ``is_spurious_cross_lang_placeholder_issue``: if ``extract_placeholders(source) == extract_placeholders(translation)`` → **keep** issue for ``apply_critic_fixes`` |
| Cyrillic in EN `` ```text `` diagrams | ``enforce_source_fenced_blocks`` copied RU fence bodies verbatim; fence-comment pass skipped ``text`` lang | Skip verbatim copy for ``text`` fences; ``translate_cyrillic_text_fences_with_client`` in finalize; blocking heuristic ``check_cyrillic_in_en_text_fences`` |
| ``toc_i.yaml`` missing ``debug-logs-otel.md`` | ``merge_en_toc_yaml`` only added RU hrefs in ``translate_hrefs`` or already on EN main — ignored RU merge-base-only pages | ``ru_base_hrefs`` param: add RU-base hrefs absent from EN main even when not in current translate set |
| ``index.md`` missing link | Same nav gap; not surfaced as 🔴 | Blocking heuristic ``check_md_link_parity`` — EN must include every RU ``.md`` link target |

**Expected after re-run:** critic applies ``s0109`` fix; TOC/index pick up ``debug-logs-otel.md``;
`` ```text `` diagram labels translated; link parity catches any remaining nav gaps.

**Tests:** ``test_identical_placeholder_sequence_mapping_not_dropped`` (#43365),
``test_enforce_source_fenced_blocks_preserves_text_fence_body``,
``test_merge_adds_ru_base_href_missing_from_en_main``,
``test_md_link_parity_flags_missing_en_link``.

**Release:** tag ``v0.1.0`` moved to this commit (Jun 2, 2026).

### 6.60. #43746 inline-code backtick render — critic fix undone by round-trip ([ydb #43746](https://github.com/ydb-platform/ydb/pull/43746))

**Context:** auto-translate from [#42856](https://github.com/ydb-platform/ydb/pull/42856) (MySQL import docs).
``doc_translate`` @ ``v0.1.0`` (§6.59) left 🔴 on ``import-mysql.md`` table cell ``s0163``:
critic flagged placeholder corruption (``⟦C3⟧`` → literal backticks) and proposed a fix, but
the PR still shipped broken EN text.

**Root cause (pipeline):** ``apply_critic_fixes`` succeeded, but ``render_markdown`` for
``InlineCode`` with ``marker_len=2`` and content `` ` `` concatenated delimiters
(`` + ` + `` → five backticks) instead of padded `` ` ``. ``gate_round_trip`` re-parsed
the broken markdown and restored a corrupt segment — verify stayed 🔴.

**Fix:** ``_render_inline_code`` in ``rendering/markdown_renderer.py`` — use padded
``{marker} {content} {marker}`` when content contains `` ` `` **or** the delimiter
substring (not only when the full marker string appears in content).

**Tests:** ``test_table_cell_backtick_inline_code_round_trip``,
``test_critic_fix_survives_table_cell_render_round_trip`` (#43746).

**Release:** tag ``v0.1.0`` moved to this commit.

### 6.61. #43860 doc_verify noise — plain index names + fence whitespace ([ydb #43860](https://github.com/ydb-platform/ydb/pull/43860))

**Context:** human EN PR for secondary-indexes auto-index section (fork
``SixOnMyface/YDBDOCS2241``). ``doc_verify`` @ ``v0.1.0`` left 🔴 with 7 skipped
critic fixes + heuristics; many were pipeline false positives.

**Root causes (pipeline):**

| Symptom | Cause | Fix |
|---|---|---|
| s0046/s0050 «Introduced ⟦C{n}⟧; source had plain text Index12» | RU prose uses plain ``Index12``; EN wraps in `` `Index12` `` → extra ⟦C⟧ in segment IR; rendered EN is correct | ``is_spurious_plain_text_wrapping_issue`` — drop when ident plain in RU, absent plain in EN segment text, tgt has **more** placeholders |
| s0069 «⟦U1⟧ replaced with ⟦U2⟧» | Critic hallucination; placeholder sequences identical | ``is_spurious_phantom_marker_swap_issue`` when ``extract_placeholders`` match + atom_map swap comment |
| «Блок кода №1» differs | Extra blank line after ``DECLARE`` in EN fence — code identical | ``_fence_diff_is_whitespace_only`` in ``fence_content_matches_source`` |

**Still author (not pipeline):** missing ``{% include not_allow_for_olap %}``,
``primary-key/row-oriented.md`` link, intro wording (sorted→indexed, make→run) —
``md_link_parity`` and meaning-drift items remain valid.

**Tests:** ``test_plain_text_index_name_wrapping_dropped``,
``test_phantom_marker_swap_dropped_when_sequences_match``,
``test_fence_content_allows_whitespace_only_diff``.

### 6.62. #44103 auto-translate — ``text`` fence QA + ``toc_p.yaml`` ``include:`` ([ydb #44103](https://github.com/ydb-platform/ydb/pull/44103))

**Context:** auto-translate from [#43530](https://github.com/ydb-platform/ydb/pull/43530)
(observability move to ``reference/ydb-sdk``). ``doc_translate`` @ ``v0.1.0`` (pre-§6.62)
left 🟡 on ``tracing/opentelemetry.md`` and shipped incomplete
``observability/toc_p.yaml`` (only ``Overview`` / ``index.md``).

**Root causes (pipeline):**

| Symptom | Cause | Fix |
|---|---|---|
| ``fence_body_copy`` block 1 in `` ```text `` `` span tree | §6.59 translates diagram labels (``← 1-я попытка`` → ``← 1st attempt``); ``check_fence_body_copy`` required byte-identical bodies | ``_fence_diff_is_text_diagram_label_translation`` in ``fence_integrity`` (same class as §6.53 mermaid) |
| EN ``observability/toc_p.yaml`` missing Logging/Metrics/Tracing | ``parse_toc_items`` only parsed ``href:``; RU parent toc uses ``include.path`` links to child ``toc_p.yaml`` files | Parse ``include.path``; ``TocTranslateScope.include_paths``; merge + validate include entries |
| ``doc_translate`` crash ``KeyError: 'href'`` in ``extra_toc_hrefs_for_pair`` | Set comprehension assumed every toc item has ``href`` after include support | ``if it.get("href")`` when building ``toc_hrefs`` |

**Expected after re-run:** 🟢 on observability bundle; parent ``toc_p.yaml`` mirrors RU
``include:`` structure with translated ``name`` labels.

**Tests:** ``test_fence_content_allows_text_diagram_label_translation``,
``test_merge_toc_include_links_for_new_observability_section``,
``test_extra_toc_hrefs_for_pair_skips_include_only_entries``.

**Release:** tag ``v0.1.0`` moved to this commit.

### 6.63. #44117 nested indented TOC — parse/merge regression ([ydb #44117](https://github.com/ydb-platform/ydb/pull/44117))

**Context:** auto-translate [#44108](https://github.com/ydb-platform/ydb/pull/44108) (re-run after §6.62)
reported 🟢 while shipping ``reference/ydb-sdk/toc_i.yaml`` as literally ``items:\n\n``.
After merge to ``main``: 44+ YFM003 ``unreachable-link`` for SDK topics; [#44117](https://github.com/ydb-platform/ydb/pull/44117)
manually restored the EN sidebar.

**Root causes (pipeline):**

| Symptom | Cause | Fix |
|---|---|---|
| ``_parse_toc_tree_block`` returns 0 nodes for ydb-sdk toc | Top-level ``- name:`` lines are indented 2 spaces; parser used ``list_indent=0`` | ``_top_level_list_indent`` + pass detected indent into ``_parse_toc_nodes_at_level`` |
| Nested merge drops gRPC children / wrong YAML shape | ``_serialize_toc_tree`` always used ``list_indent=0`` | Preserve EN main list indent when serializing merged tree |
| 🟢 false negative after empty merge | ``validate_toc_merge`` only checked flat href sets | ``collapsed_toc`` when merged entries &lt; half of EN main (≥3 entries); blocking in ``navigation_merge`` |

**Also:** ``include.path`` merge in nested tree path; ``_replace_item_name`` respects leading whitespace on ``- name:`` lines.

**Expected after re-run:** EN ``toc_i.yaml`` keeps all SDK hrefs + Observability ``include:`` link; no ``collapsed_toc`` warning.

**Tests:** ``test_parse_indented_nested_ydb_sdk_reference_toc``,
``test_merge_indented_nested_toc_adds_observability_include``.

**Release:** tag ``v0.1.0`` moved to this commit.

### 6.64. `doc_verify` critic fixes — separate fixup branch/PR (non-translation PRs)

**Problem:** §6.50 added a fork-only fixup path, but same-repo ``doc_verify`` still
pushed critic commits directly onto the verified PR head — including unmerged
author branches. Authors object to bot commits landing on their feature branches
without an explicit review PR.

**Decision:** **never** push critic fixes onto ``ctx.head_ref`` for **author/fork/manual**
PRs. Every such ``doc_verify`` run with applied fixes:

1. Resets ``ydbdoc-review/verify-{source_pr or pr_number}`` off
   ``translation_branch_base(ctx)``.
2. Commits critic fixes and pushes that branch to upstream.
3. Opens a fixup PR via ``gh.create_pull`` (base ``ctx.base_ref`` or translation branch
   per ``verify_fixup_pr_base`` — only when verifying a non-translation PR that
   targets a translation branch).
4. Posts QA report on the verified PR + link comment to the fixup PR.

**Translation PRs** use inline push instead — §6.75 (no fixup PR).

§6.52 stale-branch ``delete_branch`` before push applies to fixup runs only.

**Implementation:** ``run_doc_verify`` in ``workflow.py``,
``verify_fixup_pr_base`` in ``pr.py``, updated ``build_verify_fixup_*`` messages in
``reporting/builder.py``.

**Tests:** ``test_run_doc_verify_translation_pr_pushes_fixes_inline``;
``test_run_doc_verify_same_repo_author_pr_opens_fixup_pr``;
``test_verify_fixup_pr_base``; fork-head tests unchanged.

### 6.65. #44268 translated formula — placeholder align false C1→C2 ([ydb #44268](https://github.com/ydb-platform/ydb/pull/44268))

**Problem:** ``doc_translate`` reported 🟡 ``placeholder corruption`` (⟦C1⟧→⟦C2⟧) in
``limitations.md`` s0064 while the EN formula ``(number of nodes * 4)`` was correct.

**Root cause:** ``normalize_target_segments_to_source`` matched code atoms by exact
string. RU ``(количество узлов * 4)`` ≠ EN ``(number of nodes * 4)`` → pass 2 allocated
``⟦C2⟧`` for the EN slot. Critic/verify then saw RU ``⟦C1⟧`` vs EN ``⟦C2⟧``.

**Fix:** positional pairing in pass 2 when the segment has exactly one placeholder on
both sides; ``critic_unresolved = ok`` when all initial critic issues were spurious.

**Tests:** ``test_translated_code_formula_keeps_source_marker``,
``test_phantom_marker_swap_dropped_for_translated_formula_slot``.

### 6.66. Per-file harness — explicit steps, shared QA (translate + verify)

**Problem:** ``translate_file.py`` grew into a monolith (~400 lines) mixing parse,
translate, critic loop, heuristics, and verdict. ``doc_translate`` and ``doc_verify``
already shared logic via ``enable_translate``, but the boundary was implicit.

**Decision:** introduce ``ydbdoc_review.harness``:

| Piece | Role |
|---|---|
| ``FileRunState`` | Mutable per-file artifacts (segments, translations, critic, verdict) |
| ``HarnessContext`` | LLM client, glossary, config, batch sizes |
| ``HarnessStep`` | One stage: ``parse``, ``translate``, ``load_target``, ``round_trip``, ``critic_loop``, … |
| ``HarnessProfile`` | Ordered step list |
| ``TRANSLATE_PROFILE`` | ``parse → translate → QA tail`` (+ ``critic_feedback_retry``) |
| ``VERIFY_PROFILE`` | ``parse → load_target → QA tail`` (shared critic/heuristics tail) |
| ``FileHarness.run()`` | Execute profile; return ``FileTranslationResult`` |

**QA tail (shared):** ``round_trip → critic_loop → heuristics → verdict → report_artifacts``.

**Translate-only extra step:** ``critic_feedback_retry`` after ``critic_loop`` (see below).

``pipeline/translate_file.py`` is a thin wrapper: picks profile from
``enable_translate``, delegates to ``FileHarness``. GitHub ``workflow.py`` unchanged
(adapters stay outside harness).

**Not in scope (yet):** nothing critical — optional more YAML regression cases.

**Critic-feedback retranslate (translate profile only):**

After the first critic loop, if ``critic_unresolved`` still has segment-scoped issues and
``translation.critic_feedback_retries > 0``, ``CriticFeedbackRetryStep`` re-translates
those segments via ``critic_feedback_repair`` prompt, re-runs round-trip + critic loop
(up to N times). Default ``critic_feedback_retries: 2``; override via
``YDBDOC_TRANSLATION_CRITIC_FEEDBACK_RETRIES`` (set ``0`` to disable). Verify profile
unchanged.

**YAML regression fixtures** (``tests/harness/cases/*/case.yaml``):

| Piece | Role |
|---|---|
| ``HarnessCase`` | Parsed fixture: RU/EN markdown, profile, mocked LLM responses |
| ``load_harness_case`` / ``run_harness_case`` | Load sibling ``.md`` files, run ``FileHarness`` |
| ``assert_harness_case`` | Check verdict, critic state, per-segment placeholders |

Add a case = new directory with ``case.yaml`` + ``source.ru.md`` (+ ``target.en.md`` for
verify). No network; LLM mocked via ``llm.responses`` list. ``tests/harness/test_regression_cases.py``
parametrizes over all cases.

**PR-level harness (same §6.66):**

| Piece | Role |
|---|---|
| ``PRRunState`` | Pair contents, per-pair plans, accumulated ``pair_results`` |
| ``PRHarnessContext`` | Shared LLM client, glossary, config, analyze flag |
| ``run_pair_plan()`` | Dispatches one ``FileHarness`` run per pair plan |
| ``TRANSLATE_PR_PROFILE`` | ``plan_translate_pairs → execute_pair_plans`` |
| ``VERIFY_PR_PROFILE`` | ``plan_verify_pairs → execute_pair_plans`` |
| ``PRHarness.run()`` | Execute PR profile; return ``PRTranslationResult`` |

``pipeline/orchestrator.py`` and ``github/workflow._run_verify_pairs`` are thin wrappers
delegating to ``PRHarness`` with the appropriate profile. GitHub adapters (git push,
PR comments) stay outside harness.

**Tests:** ``tests/unit/test_harness.py``, ``tests/unit/test_harness_pr.py``,
``tests/unit/test_critic_retranslate.py``, ``tests/harness/test_regression_cases.py``;
existing ``test_translate_file.py`` / orchestrator tests use explicit env when retries
must be disabled.

**Migration:** render/finalize helpers moved to ``harness/render.py``; re-exported from
``translate_file`` for backward compatibility.

### 6.67. #44872 KV format template placeholder align ([ydb #44872](https://github.com/ydb-platform/ydb/pull/44872))

**Problem:** ``--item STRING`` paragraphs use a translated KV format spec
(``<свойство>=<значение>,...`` → ``<property>=<value>,...``). Pass 1 atom match
fails; pass 3 allocated ``⟦C5⟧``/``⟦C7⟧`` → critic blocked export-s3, import-alt,
export-nfs on [PR #44872](https://github.com/ydb-platform/ydb/pull/44872).

**Fix:** pass 2 in ``placeholder_align`` pairs unmatched code slots when both sides
match ``<…>=<…>`` KV template pattern (or single-slot segment per #44268).

**Tests:** ``test_translated_format_template_*``, ``tests/harness/cases/44872_format_template/``.

### 6.68. #44872 manual EN fixes — segment alignment + toc scope ([ydb #44872](https://github.com/ydb-platform/ydb/pull/44872))

**Context:** NFS export/import auto-translate from [#38700](https://github.com/ydb-platform/ydb/pull/38700)
(32 EN files). ``doc_verify`` @ ``v0.1.0`` (with §6.67) still surfaced contributor-side
issues while the PR was being fixed.

**Pipeline fix:** §6.67 KV format template placeholder align (``--item STRING`` paragraphs).

**Contributor pitfalls (not pipeline bugs):**

| Symptom | Cause | Remediation |
|---|---|---|
| ``segment count mismatch`` on ``concepts/backup.md``, ``devops/.../index.md`` | Manual EN edits added/removed YFM blocks (``{% note %}``, See also bullets) without preserving 1:1 segment structure vs RU | Mirror RU block boundaries in EN; do not delete notes or reorder structural elements independently |
| ``unexpected_href`` in ``en/recipes/toc_p.yaml`` | EN-only toc entry (``system-tablet-backup/index.md``) with no matching RU PR toc change | Remove EN-only href or add the equivalent RU toc entry in the same PR |
| ``md_link_parity`` for ``system-tablet-backup.md`` | RU link target moved to ``concepts/backup.md`` but EN still pointed at the old path | Update EN ``.md`` links to match RU href targets |
| Recipe pages without toc entry | Allowed — cross-link targets do not require toc | Keep recipe ``.md`` files for link parity; omit from toc when RU PR did not add them |

**Operational:** after segment-structure fixes on the PR branch, round-trip gate passed
(concepts 62=62, devops 36=36). Re-run ``doc_verify`` on the updated head.

**Report UX:** ``humanize_heuristic`` now labels ``md_link_parity`` and clarifies
``unexpected_href`` (not in RU PR diff and not EN main legacy).

### 6.69. Split ``doc_translate`` and ``doc_verify`` pipelines

**Problem:** ``doc_translate`` ran the full critic/heuristics/verdict tail inline
(``TRANSLATE_PROFILE`` = parse → translate → QA). Operators wanted translate-only
on the source PR label, then a separate ``doc_verify`` pass on the translation PR.

**Decision:**

| Stage | Profile | Steps |
|---|---|---|
| ``doc_translate`` | ``TRANSLATE_PROFILE`` | ``parse → translate`` |
| ``doc_verify`` | ``VERIFY_PROFILE`` | ``parse → load_target → round_trip → critic → heuristics → verdict`` |
| Local ``translate-file --with-critic`` | ``TRANSLATE_WITH_QA_PROFILE`` | legacy single-step QA (optional) |

After ``doc_translate`` opens/pushes the translation PR:

1. Short **handoff** comment on translation PR (not full QA report).
2. ``doc_verify`` label added via API (best-effort; may need ``YDBOT_TOKEN`` in
   ``trigger-translation-ci`` — §16.7 — because ``GITHUB_TOKEN`` label events do
   not cascade). **Superseded by §6.73** — auto verify job instead of label.

**Tests:** ``test_profiles_translate_only_verify_has_qa``,
``test_run_doc_translate_posts_comments`` (``doc_verify`` label),
``test_build_source_pr_comment_new_and_updated``.

### 6.70. ``doc_verify`` RU fallback when EN matches checkout (merged source PR, #44872)

**Problem:** [PR #44872](https://github.com/ydb-platform/ydb/pull/44872) after manual EN
fixes: ``concepts/backup.md`` and ``devops/.../index.md`` failed segment alignment
(46 vs 62). §6.31 loaded RU from **source PR #38700 head**; EN on the translation
branch was aligned to **checkout RU** (``main``, 62 segments) after the source PR
merged and contributors expanded system-tablet sections.

**Decision:** ``pick_verify_ru_text`` in ``github/pr.py`` — still prefer source PR
head when segment counts match EN; otherwise use **local checkout RU** when only it
matches EN segment count. Preserves §6.31 (90 vs 90) and fixes post-merge manual
alignment (62 vs 62).

**Tests:** ``test_pick_verify_ru_text_*``, ``test_load_verify_pair_contents_uses_local_when_api_segments_differ``,
regression with real ``backup.md`` from ``ydbdoc-review/pr-38700``.

### 6.71. Parent toc supplementation + prose angle placeholders (#44889)

**Problem:** [PR #44889](https://github.com/ydb-platform/ydb/pull/44889) translated
``system_tablet_backup_config.md`` but ``build-docs`` failed: page not in EN
``configuration/toc_p.yaml``. RU toc already on ``main`` (earlier PR); source
PR #43672 only added the ``.md``. ``doc_translate`` scope is PR-diff navigation
only. ``recovery.md`` kept ``<путь>`` in inline backticks — angle-placeholder
fix ran only inside fences (§6.39).

**Decision:**

1. ``supplement_navigation_pairs()`` — after markdown translate, for each new EN
   page walk ancestor ``toc_*.yaml``; if RU lists ``href`` and EN ``main`` lacks
   it, queue parent toc merge with ``extra_toc_hrefs`` (same as §6.17).
2. ``fix_russian_angle_placeholders_in_en()`` — apply ``<путь>`` → ``<path>`` map
   in prose/backticks too; add ``описание ошибки`` → ``error description``.

**Follow-up (§6.84):** also queue **child** toc yaml referenced via
``include.path`` from ancestor sidebars (e.g. ``sqs-api/toc_i.yaml``).

**Tests:** ``test_navigation_supplement.py``, ``test_homoglyphs`` prose backtick cases.

### 6.72. Parent toc supplement: no full §6.59 gap fill (#44916)

**Problem:** [PR #44916](https://github.com/ydb-platform/ydb/pull/44916) — §6.71
``supplement_navigation_pairs`` triggered ``configuration/toc_p.yaml`` merge for
``system_tablet_backup_config.md``, but ``merge_en_toc_yaml`` §6.59 gap-fill also
added RU-only renames ``hive_config.md``, ``kafka_proxy_config.md``,
``monitoring_config.md`` (files absent on EN ``main``) while keeping legacy
``hive.md`` / ``kafka.md`` → ``build-docs`` ENOENT.

**Decision:** ``NavigationPair.supplement_only``; supplemented merges pass
``restrict_gap_fill_to_scope=True`` to ``merge_en_toc_yaml`` — only
``translate_hrefs`` / ``extra_toc_hrefs`` are added, not every RU-base gap.

**Follow-up (§6.85):** when the EN toc file is **entirely absent**, merge uses
**full RU mirror** (``restrict_gap_fill=False``). §6.72 still applies when EN
exists but is only partially aligned (legacy ``hive.md`` vs ``hive_config.md``).

**Tests:** ``test_merge_supplement_only_adds_translated_href_not_full_ru_gap``.

### 6.82. Restrict §6.59 gap fill for all toc merges (#46258)

**Problem:** [PR #46258](https://github.com/ydb-platform/ydb/pull/46258) (translation
for [#43010](https://github.com/ydb-platform/ydb/pull/43010)) — source PR added only
**Spring** to ``integrations/toc_i.yaml``. ``merge_navigation_pair`` passed
``restrict_gap_fill_to_scope`` only when ``NavigationPair.supplement_only`` (§6.72).
Direct toc edits use ``supplement_only=False``, so §6.59 gap-fill copied every RU-base
``include.path`` missing from EN ``main`` — including
``sql-translation/toc-sql-translation.yaml`` — without creating EN files →
``build-docs`` ENOENT.

**Decision:** always pass ``restrict_gap_fill_to_scope=True`` from
``merge_navigation_pair``. Scoped adds come only from ``toc_translate_scope`` (PR
diff) plus ``extra_toc_hrefs`` / ``gap_hrefs`` for hrefs already on RU base; include
paths follow ``translate_include_paths`` only. §6.72 supplement behavior is unchanged,
just no longer the sole caller of the flag.

**Tests:** ``test_merge_direct_toc_edit_does_not_gap_fill_ru_base_includes``.

**Follow-up (§6.84–§6.85):** gap-fill restriction must not block **creating** EN
toc files that have no EN ``main`` mirror. ``_resolve_toc_merge_scope`` in
``navigation_merge.py`` disables ``restrict_gap_fill`` for absent EN sidebars
(§6.85 table).

### 6.73. Inline ``doc_verify`` after ``doc_translate`` (#44912)

**Problem:** [PR #44912](https://github.com/ydb-platform/ydb/pull/44912) had label
``doc_verify`` but no QA report. ``run_doc_translate`` added the label via
``GITHUB_TOKEN`` — GitHub does **not** cascade label events into other workflows
(§16.7). A separate CI job (``ydbdoc-verify-auto``) would fix this but requires
merging workflow changes in ``ydb-platform/ydb``.

**Decision:**

1. **Do not** add ``doc_verify`` label from ``run_doc_translate`` (action).
2. After push + translation PR open, **call ``run_doc_verify`` inline** in the
   same action process (same CI job) — full QA report on translation PR, no
   workflow changes in ``ydb``.
3. **`doc_verify` label** + ``ydbdoc-verify.yml`` — manual re-run only.
4. **`trigger-translation-ci`** (existing ydb workflow) — ``rebuild_docs`` +
   ``ok-to-test`` only via ``YDBOT_TOKEN``.

**Implementation:** ``run_doc_translate`` → ``run_doc_verify`` when translation
PR exists; ``build_source_pr_comment(..., verify_result=...)`` for QA line on
source PR.

**Tests:** ``test_run_doc_translate_posts_comments`` (inline verify mocked);
``test_build_source_pr_comment_new_and_updated``.

### 6.74. ``validate_toc_merge`` legacy href alias + scoped missing check (#44942)

**Problem:** [PR #44942](https://github.com/ydb-platform/ydb/pull/44942) — supplement
merge for ``configuration/toc_p.yaml`` was correct (``system_tablet_backup_config.md``
added, EN legacy ``hive.md`` / ``kafka.md`` preserved per §6.72), but ``doc_verify``
blocked on ``missing_href``: ``hive_config.md``, ``kafka_proxy_config.md``,
``monitoring_config.md``. RU and EN sidebars share ``name`` but divergent ``href``
basenames on EN ``main``; ``monitoring_config`` is a pre-existing RU-only gap outside
translate scope.

**Decision:**

1. **Legacy alias:** scoped RU ``href`` is covered when EN merged has the same
   ``name`` and an ``href`` that exists on EN ``main`` (legacy basename).
2. **Scoped parity only:** drop repo-wide ``ru_labels - en_labels`` ``missing_href``
   check; require mirror only for ``translate_hrefs`` / ``translate_include_paths``
   (already passed from ``toc_translate_scope`` + ``extra_toc_hrefs``).

**Implementation:** ``_en_covers_ru_href`` in ``navigation/toc.py``;
``validate_toc_merge`` ``scope_not_applied`` uses alias-aware coverage.

**Tests:** ``test_validate_toc_merge_accepts_legacy_href_alias_supplement``,
``test_validate_toc_merge_flags_scoped_href_missing_from_en``,
``test_validate_toc_merge_legacy_alias_covers_scoped_ru_rename``.

### 6.75. Translation PR: inline critic fixes (no fixup PR)

**Problem:** §6.64 opened ``ydbdoc-review/verify-{N}`` + fixup PR for every
``doc_verify``, including auto-translation PRs on ``ydbdoc-review/pr-{N}``. Reviewers
saw 🟢 on the translation PR while safe critic fixes lived in a second PR ([#45047](https://github.com/ydb-platform/ydb/pull/45047)
for [#45042](https://github.com/ydb-platform/ydb/pull/45042)) — easy to merge translation
without fixup and lose applied fixes.

**Decision:**

1. **Translation PR** (head ``ydbdoc-review/pr-{source}``): commit safe critic fixes
   **on the translation branch** (second bot commit), push ``ctx.head_ref``. **No**
   ``ydbdoc-review/verify-*`` branch, **no** fixup PR.
2. **Author / fork / manual verify PRs:** keep §6.64 fixup branch + separate PR —
   never push onto the verified head.
3. QA report is posted **after** the inline push; ``Checkout:`` in the report is the
   commit that **includes** applied critic fixes. **One** comment on the translation PR
   (full QA report only) — no extra «fixes are in this branch» note (§6.102).

**Implementation:** ``is_translation_pr_branch`` in ``pr.py``;
``run_doc_verify`` branch selection in ``workflow.py``.

**Tests:** ``test_run_doc_verify_translation_pr_pushes_fixes_inline``;
fork/author fixup tests unchanged.

### 6.76. Skip ``doc_translate`` when both RU and EN changed (bilingual PR, #44191)

**Problem:** [PR #44191](https://github.com/ydb-platform/ydb/pull/44191) updated
both RU and EN mirrors in one author PR. Auto-translate
[#45043](https://github.com/ydb-platform/ydb/pull/45043) full re-rendered EN from
RU (§6.30), overwriting the author's manual EN edits (+807/−258 on ``basic.md``).

**Decision:**

1. **Markdown pairs:** if merge-base diff shows **both** ``ru_changed`` and
   ``en_changed`` → ``plan_pair_heuristic`` returns ``skip`` (no LLM, no commit).
2. **Navigation YAML:** ``build_navigation_pairs`` tracks ``en_changed`` for
   completeness / verify scope. **Superseded for merge by §6.123** — do **not**
   skip ``run_navigation_merges`` when both sides changed (partial EN toc edits
   left orphans, #41271 / #47104).
3. **Completeness:** ``bilingual_en_mirrors`` excludes those EN paths from
   ``completeness_gaps`` — no false «не переведён» on bilingual PRs.
4. **Reporting:** ``build_source_pr_comment`` — «перевод не требуется», no
   translation PR when all pairs are bilingual skip.

**Implementation:** ``pipeline/analyze.py`` (``BILINGUAL_SKIP_SUMMARY``),
``pipeline/pairs.py``, ``navigation_merge.py``, ``completeness.py``,
``reporting/builder.py``.

**Tests:** ``test_heuristic_both_changed_skip_bilingual``,
``test_build_navigation_pairs_tracks_en_side_changed``,
``test_completeness_ok_when_bilingual_skip``,
``test_build_source_pr_comment_bilingual_skip``.

### 6.77. Translation PR ``doc_verify`` scope (#45053)

**Problem:** Inline ``doc_verify`` on translation PRs checked EN files and parent
``toc_*.yaml`` outside the translation commit (e.g. ``spilling.md``,
``export-import/toc_i.yaml`` from supplement / stale fixup), producing false 🔴.

**Decision:**

1. On translation PR (``ydbdoc-review/pr-{N}``): verify **only** markdown pairs
   whose **EN** path is in the PR diff vs base.
2. Navigation: only EN toc/redirect files in the PR diff; **no**
   ``supplement_navigation_pairs`` on translation PR verify.
3. ``supplement_only`` ancestor tocs are excluded from verify.

**Implementation:** ``filter_translation_pr_verify_scope`` in ``pipeline/pairs.py``;
``run_doc_verify`` in ``workflow.py``.

**Tests:** ``test_filter_translation_pr_verify_scope_keeps_en_diff_only``.

### 6.78. English YFM heading anchors + hallucinated link repair (#45053)

**Problem:** RU headings like ``{#fields-Описание}`` stayed in segment text (parser
only split ASCII anchors); LLM translated to ``{#fields-Description}``. List items
gained spurious ``[Grace Hash Join](⟦U1⟧)`` with no source URL atom.

**Decision:**

1. Parse any ``{#…}`` suffix into ``Heading.anchor`` (Cyrillic allowed).
2. On EN render: ``english_yfm_anchor`` maps ``fields-Описание`` →
   ``fields-Description`` from translated heading text.
3. Strip model-copied ``{#…}`` from heading segment translations.
4. ``_strip_hallucinated_url_links`` removes ``[text](⟦U⟧)`` when source has no
   URL placeholder; critic filter ``is_spurious_hallucinated_link_issue``.

**Implementation:** ``validation/yfm_anchor.py``, ``markdown_parser.py``,
``markdown_renderer.py``, ``placeholder_repair.py``, ``placeholder_drift.py``.

**Tests:** ``test_yfm_anchor.py``, ``test_strip_hallucinated_url_link_*``,
``test_hallucinated_link_dropped_*``.

### 6.79. Cyrillic homoglyphs in tab title whitelist (#45053)

**Problem:** RU docs use ``С++`` (Cyrillic U+0421) as a tab title. Whitelist
knows ``c++`` (Latin) only → RU emits ``TAB_TITLE`` segment, EN does not →
``gate_round_trip`` 🔴 on ``balancing-prefer-*.md`` ([#45053](https://github.com/ydb-platform/ydb/pull/45053)).

**Decision:** ``normalize_confusable_cyrillic`` (``homoglyphs.py``) before tab
whitelist lookup in ``extractor._is_whitelisted_tab_title``.

**Tests:** ``test_extract_cyrillic_cpp_tab_title_whitelisted``,
``test_extract_nested_tabs_ru_en_same_segment_count_with_cyrillic_cpp``.

### 6.80. Locale include dependency closure (#44880 / #45056)

**Problem:** Source PR #44880 extracted «additional parameters» into new locale
includes ``export-additional-params.md`` / ``import-additional-params.md``.
``doc_translate`` translated parent ``export-s3.md`` / ``import-s3.md`` / ``nfs``
(preserving ``{% include … %}``) but omitted the new child include files.
Translation PR #45056 passed ``doc_verify`` 🟢; Diplodoc build failed on missing
EN include targets.

**Root cause:**

1. ``doc_translate`` scope = flat git merge-base diff only — no transitive
   closure over ``{% include %}`` references from changed RU ``.md``.
2. Git diff can miss paths that GitHub PR Files API still lists (post-merge /
   squash edge cases).
3. ``doc_verify`` (§6.77) checks only EN files in the translation PR diff; no
   validation that locale-relative include targets exist on disk.
4. ``completeness_gaps`` blocked merge in reports but did not block push; inline
   ``doc_verify`` ignored translate-time gaps.

**Decision:**

1. **`supplement_include_pairs()`** — after ``build_doc_pairs``, BFS RU markdown
   in scope; parse ``YfmInclude``; resolve paths under ``docs/ru/…/_includes/``;
   add missing RU/EN pairs + synthetic change entries for ``completeness_gaps``.
2. **Scope union** — ``merge_pr_file_changes(git diff, GitHub PR files API)`` in
   ``run_doc_translate``.
3. **`check_missing_locale_include_targets()`** — blocking ``include_target:`` in
   ``doc_verify`` (and inline verify after translate).
4. **Push gate** — skip commit/push when ``completeness_gaps`` non-empty; propagate
   gaps into inline verify report; source PR short comment shows 🔴 when gaps.

**Implementation:** ``parsing/include_paths.py``, ``pipeline/include_supplement.py``,
``validation/include_targets.py``, ``github/pr.merge_pr_file_changes``,
``github/workflow.py``.

**Tests:** ``test_include_paths.py``, ``test_include_supplement.py``,
``test_include_targets.py``, ``test_merge_pr_changes.py``.

**Follow-up (§6.80.1):** ``collect_yfm_includes`` uses line regex, not full
``parse_markdown`` — bare bullet-list include fragments (``*-additional-params.md``)
crash mdit with spurious ``front_matter`` inside nested list items.

**Follow-up (§6.80.2):** ``_parse_block`` skips spurious ``front_matter`` tokens
(re-parse next block) so ``doc_translate`` can parse/translate bullet-list include
fragments. Fixtures: ``tests/fixtures/44880/*.ru.md`` from PR #44880.

**Follow-up (§6.80.3):** ``supplement_include_pairs`` adds transitive locale includes
only when EN mirror is **absent** at merge-base (or RU path is in source PR diff
seed). Skips existing EN snippets already on ``main`` — avoids translating 20+
unchanged includes from ``export-s3.md``.

**Follow-up (§6.80.4):** Source PR comment when push blocked: «translation PR не
создан», completeness gap list, pipeline errors — not misleading «перевод готов».

**Follow-up (§6.80.5):** [ydb #43997](https://github.com/ydb-platform/ydb/pull/43997) —
recipe pages reference shared Go snippets as ``../../../_includes/go/…`` which
mis-resolves to ``docs/{ru,en}/_includes/…`` instead of language-neutral
``docs/_includes/…``. Pipeline queued false RU↔EN pairs → ``Missing source text``
+ completeness gate blocked push (translation PR never created).

Fix: ``include_paths._locale_root_shared_include_resolved()`` returns ``None``;
``completeness.is_misresolved_shared_include_mirror()`` excludes false EN mirrors
from §6.80 gaps. Re-trigger: move ``@v0.1.0`` tag + toggle ``doc_translate`` label.

**Follow-up (§6.80.6):** [ydb #46435](https://github.com/ydb-platform/ydb/pull/46435),
[#46431](https://github.com/ydb-platform/ydb/pull/46431) — auto-translate **did run**
(translation PRs created, 14 / 4 files). 🔴 from ``glossary.md`` placeholder
``atom_map`` noise after ``doc_verify`` + real issues in ``execution_process.md``
(Wikipedia links on #46431). Fix: ``placeholder_align._pair_unmatched_by_kind`` for
``⟦U⟧`` slots; report shows **Оригинал / Перевод / Почему 🔴** per segment (§17.2).

### 6.81. Trailing ``//`` fence comments + multi-comment pipeline tests (#44758)

**Problem:** §6.39/§6.46 translated only line-start ``//`` / ``#`` / ``--`` comments.
Go/C++/Java style ``panic(err) // комментарий`` on the same line was copied verbatim
from RU with Cyrillic; ``cyrillic_in_fence`` did not fire (no line-start marker).

**Decision:**

1. **`_SLASH_TRAILING_COMMENT`** in ``validation/fence_comments.py`` — match
   ``\s//\s*`` after code on the same line (whitespace before ``//`` avoids
   ``grpcs://`` URLs in strings).
2. Shared **`_trailing_comment_match``** / **`trailing_comment_code_prefix``** for
   SQL ``--`` and slash ``//`` trailing forms.
3. **`_fence_diff_is_comment_translation_only`** — when diff is on a trailing
   comment line, require **identical code prefix** before ``//``/``--`` so
   ``x := 1 // ru`` vs ``y := 1 // en`` is not treated as comment-only translation.

**Pipeline invariant (unchanged, now tested):** fenced blocks are **not** segmented;
prose is translated via segment LLM; ``finalize_en_target`` copies fence bodies from
RU, then **one JSON batch** translates all Cyrillic comment lines (line-start and
trailing) per file.

**Tests:** ``test_collect_trailing_slash_comment_on_code_line``,
``test_translate_trailing_slash_comment_preserves_code``,
``test_fence_content_allows_trailing_slash_comment_translation``,
``test_translate_pipeline_prose_then_multiple_fence_comments``,
``test_fenced_code_excluded_from_segments_only_prose_translated``.

### 6.83. EN toc target existence + ``rebuild_docs`` checkout (#45157 / #46258)

**Problem:** [PR #45157](https://github.com/ydb-platform/ydb/pull/45157) (translation
for [#31195](https://github.com/ydb-platform/ydb/pull/31195)) — ``doc_verify`` 🟢,
``rebuild_docs`` 🔴. Two gaps:

1. **CI:** ``docs_build_rebuild.yaml`` ran ``diplodoc-platform/docs-build-action``
   without ``actions/checkout`` and with a step that never set ``id: sha`` — revision
   ``pr-{N}-`` and local ``./ydb/docs`` missing → ``ENOENT …/ydb/docs`` in 14 ms.
   Inline ``doc_verify`` cannot see this; merge happened without a real docs build.
2. **Pipeline:** §6.82 stops gap-filling phantom ``include.path`` entries, but
   ``doc_verify`` still did not assert that EN toc ``href`` / ``include.path`` targets
   exist — same class as ``build-docs`` ENOENT on
   ``sql-translation/toc-sql-translation.yaml`` ([#46258](https://github.com/ydb-platform/ydb/pull/46258)).

**Decision:**

1. **`check_missing_toc_targets``** in ``validation/toc_targets.py`` — for changed EN
   toc YAML, resolve every ``href`` and ``include.path`` (including on ``href`` items)
   relative to the toc file; block when the EN mirror file is absent. Same-batch
   outputs count via ``pending_paths`` (e.g. new ``diagnostics.md`` before push).
2. Hook in ``run_doc_verify`` after navigation verify (with ``apply_include_target_checks``).
3. **ydb fix (separate PR):** restore #43222 design for ``docs_build_rebuild.yaml`` —
   dispatch-only (no checkout / inline build in ``pull_request_target``); preview
   via ``docs_preview.yaml`` on ``Build documentation`` only ([#46330](https://github.com/ydb-platform/ydb/pull/46330)).

**Tests:** ``test_toc_targets.py``; §6.82 regression
``test_merge_direct_toc_edit_does_not_gap_fill_ru_base_includes``.

### 6.84. Inline toc ``include`` + child toc supplementation (#46338)

**Problem:** [PR #46338](https://github.com/ydb-platform/ydb/pull/46338) (SQS docs for
[#44820](https://github.com/ydb-platform/ydb/pull/44820)) — ``doc_verify`` 🟢,
``build-docs`` 🔴 ``ENOENT: en/reference/sqs-api/toc_i.yaml``. RU ``toc_p.yaml``
lists ``- include: { mode: link, path: toc_i.yaml }``; EN ``toc_p`` was merged
with that include, but ``toc_i.yaml`` never landed in EN.

Two gaps in §6.83:

1. **Parse:** ``collect_toc_link_targets`` only matched block-style ``path:`` under
   ``include:``; inline ``include: { … path: toc_i.yaml }`` and include-only items
   (no ``name:``) were invisible — ``doc_verify`` did not block the broken toc.
2. **Supplement:** ``supplement_navigation_pairs`` only queued parent tocs when a
   translated page ``href`` was missing on EN ``main``. When EN ``toc_p`` already
   had ``index.md`` but lacked the child include target, ``toc_i.yaml`` was never
   queued for merge.

**Decision:**

1. ``iter_toc_include_paths`` / ``_iter_toc_include_paths`` in ``navigation/toc.py``
   — regex for inline and include-only ``include.path``;
   ``collect_toc_link_targets`` scans full yaml text (not only parsed ``- name:``
   items).
2. ``_supplement_included_child_tocs`` in ``navigation_supplement.py`` — after
   href-based parent supplement (§6.71), scan **all ancestor tocs** of translated
   pages for child ``*.yaml`` includes; queue ``NavigationPair`` when RU child
   exists and EN child is absent at merge-base. Iterates for nested includes.

**Implementation:** ``navigation/toc.py`` (``iter_toc_include_paths``,
``toc_entry_paths``), ``validation/toc_targets.py`` (uses ``collect_toc_link_targets``),
``pipeline/navigation_supplement.py``.

**Tests:** ``test_collect_toc_link_targets_reads_inline_include_only_item``,
``test_check_missing_toc_targets_detects_inline_include_child``,
``test_supplement_adds_included_child_toc_when_parent_lists_page``.

### 6.85. Mirror absent EN toc from RU (#46349)

**Problem:** [PR #46349](https://github.com/ydb-platform/ydb/pull/46349) — after §6.84
``toc_i.yaml`` was created, but ``toc_p.yaml`` merged as empty ``items:`` and
``doc_verify`` blocked with ``empty_toc``. RU ``sqs-api/toc_p.yaml`` exists on
``main``; EN mirror is absent. ``supplement_only`` pair had **empty translate
scope** (``ru_base == ru_pr``) and ``restrict_gap_fill_to_scope=True`` → merge
emitted no entries.

**Operational rule (authoritative):**

| EN ``main`` state | Merge behaviour |
|-------------------|-----------------|
| File absent / empty ``items:`` | **Full mirror** of RU sidebar: all ``href`` + ``include.path``, translate labels, ``restrict_gap_fill=False`` |
| Partial EN (§6.71 ``supplement_only``) | Add only RU entries **missing** from EN (href or include); do **not** rename legacy EN href aliases (§6.72) |
| PR diff on toc (direct edit) | Scoped merge + ``restrict_gap_fill=True`` (§6.82); only PR-scope hrefs/includes added |

**Decision:**

1. ``en_toc_is_absent`` + ``_resolve_toc_merge_scope`` in ``navigation_merge.py``.
2. Block toc parser: ``- include: { … }`` is a **separate** list item (§6.84 parser);
   include-only items copy without ``name`` translation.
3. Public helpers: ``en_toc_is_absent``, ``toc_entry_paths``, ``iter_toc_include_paths``.

**Implementation:** ``navigation/toc.py`` (``_parse_toc_items_block`` rewrite,
``en_toc_is_absent``), ``pipeline/navigation_merge.py`` (``_resolve_toc_merge_scope``,
``_toc_label_names``).

**Tests:** ``test_merge_navigation_pair_mirrors_absent_en_toc_from_ru``,
``test_parse_toc_items_reads_include_only_entry``,
``test_merge_en_toc_mirrors_absent_en_from_ru_with_inline_include``.

**Canonical case:** SQS API docs — ``ydb/docs/ru/core/reference/sqs-api/toc_p.yaml``
on ``main``, no EN mirror; translation from [#45181](https://github.com/ydb-platform/ydb/pull/45181)
→ [#46349](https://github.com/ydb-platform/ydb/pull/46349).

### 6.86. Indented block toc ``href`` parse (#46346)

**Problem:** [PR #46346](https://github.com/ydb-platform/ydb/pull/46346) —
``doc_verify`` 🟢, ``build-docs`` 🔴 ``YFM003 unreachable-link`` on
``sqs-api/index.md`` → ``auth.md`` / ``examples.md``. EN ``toc_i.yaml`` merged
as empty ``items:`` while RU on ``main`` has:

```yaml
items:
  - name: Аутентификация
    href: auth.md
```

Block parser matched ``href:`` only at exactly two spaces (``^  href:``); real
files use list indent + deeper ``href:`` (four spaces). ``parse_toc_items`` returned
``[]`` for RU → merge empty → ``empty_toc`` check skipped (``ru_items`` also empty).

**Decision:**

1. ``_first_href_in_block`` uses ``_HREF_INDENTED`` (any indent) in
   ``_parse_toc_items_block``.
2. ``_toc_nav_paths_from_text`` — raw-yaml href/include fallback for validation.
3. ``validate_toc_merge`` flags ``empty_toc`` when raw RU has nav paths but EN
   merged does not (even if block parse returns no items).

**Tests:** ``test_parse_toc_items_reads_indented_list_href``,
``test_merge_en_toc_mirrors_indented_absent_en_toc_i``,
``test_validate_toc_merge_empty_en_blocks_when_ru_has_indented_hrefs``.

### 6.87. ``toc_translate_scope`` tolerates include-only items (#46378 / #46380)

**Problem:** [PR #46378](https://github.com/ydb-platform/ydb/pull/46378) and
[#46380](https://github.com/ydb-platform/ydb/pull/46380) — translation PRs were
created, but the `doc_translate` job failed **before posting** the inline
`doc_verify` report. Root cause: `toc_translate_scope()` assumed every
`include_path` item has a `name` field; include-only lines
(`- include: { ... path: ... }`) have no `name` → `KeyError: 'name'` during
navigation verify inside inline `doc_verify`.

**Decision:** treat missing `name` as empty string for scope-diff comparisons:
use `prev.get("name","") != it.get("name","")` for both href and include paths.
This keeps scope detection semantics while never crashing.

**Tests:** ``test_toc_translate_scope_handles_include_only_items_without_name``.

### 6.88. Eliza internal route + env-only OAuth (v0.2.0)

**Problem:** First Eliza integration used OpenAI-compat URL
``{root}/raw/openai/v1`` with ``model`` in the request body. Internal models
(``deepseek-v4-flash``, ``gpt-oss-120b``) reject that vendor with
``model … is not available for vendor "openai"``. OpenAI SDK also sends
``Authorization: Bearer …`` by default.

**Decision:**

1. **Route:** ``POST {ELIZA_API_ROOT}/raw/internal/{model_id}/v1/chat/completions`` —
   one base URL per role; **no** ``model`` in JSON body.
2. **Auth:** ``Authorization: OAuth <token>`` only; token read strictly from env
   ``ELIZA_OAUTH_TOKEN`` via ``Secrets`` — never CLI argv, never YAML, never URL,
   never logs/reports.
3. **Transport:** ``ElizaLLMClient`` uses one ``requests.Session`` per client
   (``session.post``, TLS via ``YDBDOC_ELIZA_CA_BUNDLE`` / ``REQUESTS_CA_BUNDLE``;
   never ``verify=False``) — not OpenAI SDK, to avoid Bearer injection.
4. **Defaults** when ``YDBDOC_MODEL_PROVIDER=eliza``:
   ``YDBDOC_MODEL_TRANSLATE=deepseek-v4-flash``,
   ``YDBDOC_MODEL_CHECK=gpt-oss-120b`` (overridable via env).
5. **Retries:** same ``llm.retries`` backoff on 408/5xx and transient network errors;
   HTTP **429** uses ``llm.retries.rate_limit`` (separate budget) and honors
   ``Retry-After``; ``requests.SSLError`` (TLS/cert) is **fail-fast**.
   Eliza model chains do not inherit YAML Yandex fallbacks — only
   ``YDBDOC_ELIZA_*_FALLBACKS`` when confirmed internal ids exist.
6. **Compatibility:** default provider remains ``yandex_cloud``; ``ydb`` Actions
   unchanged.

**External integration (Reactor/Nirvana):** parent passes all secrets in
``subprocess.run(..., env=…)`` — see **06-llm-config** §13.6.3. Entrypoint:
``python -m ydbdoc_review job --mode translate|verify``.

**Implementation:** ``llm/client.py`` (``ElizaLLMClient``), ``config/loader.py``
(``require_eliza_api_root``, ``ELIZA_OAUTH_TOKEN``).

**Tests:** ``tests/unit/test_llm_eliza_internal.py`` (URL path, OAuth header,
no ``model`` in body, retry on 503).

### 6.89. Supplement translate queue from sidebar ``href`` targets (#46386)

**Problem:** [PR #46386](https://github.com/ydb-platform/ydb/pull/46386) (translation
for [#45181](https://github.com/ydb-platform/ydb/pull/45181)) — only ``topic.md`` and
``diagnostics.md`` changed in the source PR, but §6.84–§6.85 queued ``sqs-api``
``toc_p.yaml`` / ``toc_i.yaml`` (full RU mirror via ancestor ``include.path`` from
``reference/toc_p.yaml``). EN sidebars list ``index.md``, ``auth.md``, ``examples.md``,
yet those RU pages were never in the PR diff → ``missing_toc_target`` 🔴.

**Root cause:** ``doc_translate`` only translates ``.md`` from the source PR file list
(+ locale ``{% include %}`` deps via §6.80). Mirroring navigation does **not** imply
translating every ``href`` the sidebar will expose.

**Decision:** after ``supplement_navigation_pairs``, scan all queued RU toc YAML
(including child ``include.path`` sidebars) for ``href: *.md`` targets. When RU page
exists and EN mirror is absent at ``merge_base_with``, add ``DocPair`` and run a
second markdown translation pass before ``run_navigation_merges``. Same contract as
§6.80 include supplementation.

**Implementation:** ``pipeline/toc_href_supplement.py`` (``supplement_toc_href_pairs``),
``github/workflow.py`` (``_translate_additional_pairs``).

**Tests:** ``tests/unit/test_toc_href_supplement.py``.

**Follow-up (§6.90):** after toc-href pairs are added, run ``supplement_include_pairs``
again before the second translation pass — otherwise locale ``{% include %}`` snippets
referenced by mirrored pages (e.g. ``sqs-api/_includes/limitations.md`` in #46393)
stay untranslated and ``include_target`` blocks verify.

### 6.90. Include supplementation after toc-href pages (#46393)

**Problem:** [PR #46393](https://github.com/ydb-platform/ydb/pull/46393) — §6.89
translated ``sqs-api/index.md`` and ``examples.md``, but ``doc_verify`` 🔴 on
``include_target``: missing ``_includes/limitations.md`` and
``_includes/examples_prerequisites.md``.

**Root cause:** ``supplement_include_pairs`` ran only on initial PR-diff pairs,
before §6.89 added toc-href pages; the second markdown pass skipped include closure.

**Decision:** after ``supplement_toc_href_pairs``, call ``supplement_include_pairs``
again, merge synthetic changes, then translate all new pairs in one second pass.

**Implementation:** ``github/workflow.py``.

**Tests:** ``test_toc_href_then_include_supplement_closes_sqs_api_includes``.

### 6.91. Unified navigation scope supersedes §6.71–§6.90 (Phase J, 2026-07-14)

**Problem:** §6.71–§6.90 patched TOC scope incrementally (three supplement modules,
multi-pass ordering in ``workflow.py``, ``extra_toc_hrefs`` axis). Each fix worked
in isolation but the combination drifted between ``doc_translate`` and ``doc_verify``.

**Decision:** **09-navigation-scope** §22 — ``plan_translation_scope()`` builds
``TranslationScopePlan`` once; translate, merge, and verify consume the same object.
Legacy supplement modules removed in commit ``d68812f``.

**Historical §6.71–§6.90** entries below remain for regression context and PR links.
For current behavior, read §22 first.

**Tests:** ``tests/unit/test_nav_scope_planner.py``, ``test_navigation_merge_pipeline.py``
(scope_plan merge), ``test_navigation_verify.py`` (scope_plan verify).

### 6.92. §22 step-3 scope overreach (#46451, #46454, #46461)

**Problem:** First §22 rollout (2026-07-14) — translation PRs listed 35 / 49 / 51 files
for source PRs that changed only a handful of RU paths. Spurious pages (postgresql,
public-materials, hive_config, …) came from step 3: “for each discovered toc, queue
every ``href`` missing on EN at merge-base.”

**Decision:** step 3 applies **per sidebar** (§22.4, §22.5):

- Absent EN toc → full mirror of that toc’s hrefs (unchanged §6.85).
- Toc **in PR diff** → **new** hrefs only (RU base vs PR head via ``read_ru_base``).
- Partial EN sidebar → missing EN mirrors for **diff pages listed in that toc** (§6.72).

**Implementation:** ``navigation/scope_planner.py`` (``caff954``); workflow passes
``read_ru_base`` from ``make_repo_scope_readers()``.

**Tests:** ``case_44457`` in ``tests/fixtures/nav_cases/``,
``test_case_44457_scoped_to_diff_not_whole_menu``.

### 6.93. ReportArtifactsStep import regression (#44457 re-run)

**Problem:** Re-run ``doc_translate`` on [#44457](https://github.com/ydb-platform/ydb/pull/44457)
after ``c2d713f`` crashed with ``NameError: build_segment_source_excerpts`` in
``ReportArtifactsStep.run`` — call added without import.

**Decision:** import ``build_segment_source_excerpts`` from ``reporting.locations`` in
``harness/steps.py``.

**Implementation:** ``c32479a``.

**Tests:** harness/report artifact tests (15 failures before fix).

### 6.94. Glossary MD037 bold-link postprocess (#46451 build-docs)

**Problem:** [#46451](https://github.com/ydb-platform/ydb/pull/46451) —
``build-docs`` failed on six **MD037** warnings in ``glossary.md``. RU source uses
``**[term](url)**``; translator often inserts a space: ``** [term](url)**``.

**Decision:** deterministic postprocess in ``finalize_en_target`` →
``postprocess_en_target_markdown`` → ``fix_no_space_in_emphasis()`` replaces
``** [`` with ``**[`` (markdownlint MD037).

**Implementation:** ``validation/markdown_layout.py``, ``validation/homoglyphs.py``
(``55ba789``).

**Tests:** ``test_postprocess_fixes_bold_link_md037`` in ``test_homoglyphs.py``.

### 6.95. Eliza transport hardening + finalize skip warnings (2026-07-14)

**Problem:** Eliza CI runs hit duplicate ``basicConfig`` logging, retried TLS/cert
errors, opaque 429 backoff, and silent skips when fence/prose finalize could not call
the LLM.

**Decision:**

1. **Logging:** configure CLI logging once (``cli.py``).
2. **TLS:** ``requests.SSLError`` → immediate ``LLMRequestError`` (no retry); hint
   ``YDBDOC_ELIZA_CA_BUNDLE`` / ``REQUESTS_CA_BUNDLE``.
3. **429:** separate ``llm.retries.rate_limit`` budget; honor ``Retry-After`` header
   (``llm/retry.py``).
4. **4xx:** fail-fast on 400/401/403/404; sanitize token from error text.
5. **Finalize skips:** ``validation/finalize_skips.py``; ``out_warnings`` from fence/prose
   finalize → ``state.finalize_warnings`` → heuristics bucket in ``HeuristicsStep``.

**Implementation:** ``c6cd916`` (logging, TLS fail-fast), ``55ba789`` (429, 4xx, finalize warnings).
Superseded for TLS routing by §6.99 (`llm/tls.py`); §6.98 adds overloaded failover.

**Tests:** ``test_llm_eliza_internal.py``, ``test_llm_retry.py``, ``test_fence_comments.py``.

### 6.96. Report UX: source + translation + problem + suggestion (2026-07-14)

**Problem:** Translation PR reports showed «Почему 🔴/🟡» without RU/EN context; Wikipedia
``link_locale`` heuristics did not deep-link to the offending line.

**Decision:**

1. **«Что исправить»** items use **Оригинал / Перевели / Проблема / Совет** (not «Почему 🔴»).
2. ``reporting/heuristic_context.py`` — excerpt RU + EN from disk for ``link_locale``;
   line number + GitHub blob URL in problem text for Wikipedia manual-fix hints.
3. ``heuristic_messages.py`` — wiki-specific problem + suggestion strings.

**Implementation:** ``203956a`` — ``builder.py``, ``heuristic_context.py``, tests
``test_heuristic_context.py``, ``test_reporting_builder.py``.

### 6.97. Text-fence batch JSON parsing (2026-07-14)

**Problem:** ``translate_cyrillic_text_fences`` called ``json.loads()`` on raw LLM output
still wrapped in `` ```json `` fences → batch skipped, Cyrillic left in `` ```text `` blocks.

**Fix:** ``_strip_json_code_fence``, ``_parse_batch_translate_response`` in
``validation/fence_comments.py``; sync ``translate_cyrillic_text_fences()``.

**Implementation:** ``203956a``. Golden: ``test_fence_comments.py`` (Полная копия → Increment₁).

### 6.98. Eliza 429 overloaded → model fallback (2026-07-14)

**Problem:** Local Eliza runs hit ``HTTP 429: model … is overloaded``; translator pinned
``model=primary`` so ``YDBDOC_ELIZA_TRANSLATE_FALLBACKS`` never ran; 6× retry on same
saturated model wasted minutes.

**Decision:**

1. **Translator** (``translation/translator.py``): on ``LLMRetryExhaustedError`` with
   rate-limit, try ``model_chain[1:]`` (same pattern as placeholder-mismatch fallback).
2. **Eliza client** (``llm/client.py``): when 429 body contains ``overloaded``, **one**
   attempt per model then advance chain (not 6× sleep on same slug).
3. **Env:** ``YDBDOC_ELIZA_TRANSLATE_FALLBACKS=gpt-oss-120b`` (comma-separated confirmed ids).

**Tests:** ``test_translate_batch_rate_limit_tries_fallback_model``,
``test_eliza_429_overloaded_*`` in ``test_llm_eliza_internal.py``.

### 6.99. TLS: public GitHub vs internal Eliza CA (2026-07-14)

**Problem:** Setting ``REQUESTS_CA_BUNDLE=/etc/ssl/certs/YandexInternalCA.pem`` globally
(in ``~/.zshrc``) broke **``api.github.com``** (`unable to get local issuer certificate`).
Using internal CA **only** for Eliza without certifi broke Eliza chains that need public roots.

**Decision:**

1. **`llm/tls.py`:** ``public_ca_bundle()`` → always **certifi** (ignores ``REQUESTS_CA_BUNDLE``).
2. **`github/client.py`:** ``verify=public_ca_bundle()`` on every REST call.
3. **`eliza_tls_verify()`:** merge **certifi + internal PEM** (``YDBDOC_ELIZA_CA_BUNDLE`` or
   default ``/etc/ssl/certs/YandexInternalCA.pem``); cached under ``~/.cache/ydbdoc-review/``.
4. **Do not** set ``REQUESTS_CA_BUNDLE`` to internal-only CA in shell profile.

**Env (local):** ``YDBDOC_ELIZA_CA_BUNDLE=/etc/ssl/certs/YandexInternalCA.pem`` in
``ydbdoc-review/.env``; Eliza OAuth in ``~/.zshrc`` (``ELIZA_OAUTH_TOKEN``).

**Tests:** ``test_llm_tls.py``.

### 6.100. CLI cooperative shutdown (2026-07-14)

**Problem:** ``Ctrl+C`` did not stop long ``job`` runs — main blocked on
``ThreadPoolExecutor`` / worker ``time.sleep()`` during 429 backoff.

**Decision:** ``shutdown.py`` — ``SIGINT``/``SIGTERM`` → ``request_shutdown()``;
``interruptible_sleep()`` in Eliza retry loop; cancel futures on ``KeyboardInterrupt`` in
``translate_segments``; ``install_shutdown_handlers()`` in ``cli.py`` callback.

**Kill fallback:** ``pkill -9 -f ydbdoc_review`` or ``pkill -9 -f 'python -m ydbdoc_review'``
(from another terminal); patterns ``ydbdoc-review job`` do **not** match ``ydbdoc_review``.

**Implementation:** ``shutdown.py``, ``cli.py``, ``llm/client.py``, ``translation/translator.py``.

**Tests:** ``test_shutdown.py``.

### 6.101. ``format_heuristic_location`` ``file_url`` crash (#46475, 2026-07-14)

**Problem:** CI run [29336311628](https://github.com/ydb-platform/ydb/actions/runs/29336311628)
on translation PR [#46475](https://github.com/ydb-platform/ydb/pull/46475) — translate +
inline ``doc_verify`` completed (12 files pushed, critic fixes applied), then
``build_full_report`` crashed:

``AttributeError: 'ReportLinkContext' object has no attribute 'file_url'``

Regression in ``203956a`` (§6.96): ``heuristic_context.format_heuristic_location`` called
nonexistent ``link.file_url()`` instead of ``locations.format_line_ref()``.

**Decision:** reuse ``format_line_ref()`` for GitHub blob deep links (same as critic items).

**Mitigation without full re-translate:** after tag bump, label translation PR with
**``doc_verify``** only — skips LLM translate, re-runs critic + heuristics + report
(§6.73, ``ydbdoc-verify.yml``).

**Tests:** ``test_format_heuristic_location_github_link`` in ``test_heuristic_context.py``.

### 6.102. Drop redundant inline-fix comment on translation PR (2026-07-15)

**Problem:** After ``doc_verify`` on a translation PR, workflow posted **two** comments:
the full QA report plus ``build_verify_translation_inline_comment`` («Безопасные
автоисправления добавлены коммитом в эту ветку…»). The second message duplicated
information already in the report (``Checkout:`` SHA after critic push, «Что исправить»,
commit message ``Apply critic fixes from doc_verify``).

**Decision:** remove the extra comment; translation PR gets **only** ``build_full_report``.
Fixup-path comment (§6.64, link to separate fixup PR on author/fork PRs) unchanged.

**Implementation:** ``run_doc_verify`` in ``workflow.py``; deleted
``build_verify_translation_inline_comment`` from ``reporting/builder.py``.

**Tests:** ``test_run_doc_verify_translation_pr_pushes_fixes_inline`` — one
``post_issue_comment`` call, no «коммитом в эту ветку» text.

### 6.103. Eliza ordered model chains for translate/critic (2026-07-15)

**Problem:** §6.98 added overloaded fast-fail and env fallbacks, but ``ElizaLLMClient``
often returned a **single** model slug; chain advance applied only to 429 ``overloaded``,
not to full 429/5xx/unavailable exhaustion. Nirvana needs env-only chain config without
code changes.

**Decision:**

1. **``llm.eliza`` in ``default.yaml``** — separate from Yandex ``llm.models``:
   translate ``deepseek-v4-flash → gpt-oss-120b → gpt-oss-20b``;
   critic ``gpt-oss-120b → gpt-oss-20b → deepseek-v4-flash`` (light → heavy).
2. **Env overrides (Nirvana / local):**
   - primary: ``YDBDOC_MODEL_TRANSLATE`` / ``YDBDOC_MODEL_CHECK``
   - fallbacks CSV: ``YDBDOC_ELIZA_TRANSLATE_FALLBACKS`` / ``YDBDOC_ELIZA_CHECK_FALLBACKS``
     (legacy alias ``YDBDOC_ELIZA_CRITIC_FALLBACKS``)
3. **``model_chain_for_role()``** returns ``[primary, *fallbacks]`` deduped; Yandex YAML
   fallbacks still **ignored** for Eliza provider.
4. **``ElizaLLMClient.chat()``** — per model: existing retry/backoff budgets; advance to
   **i+1** when retries exhausted on **429**, **5xx**, timeout/connection, model unavailable.
   **Do not** advance on **4xx** fail-fast or HTTP-200 parse/format errors (empty choices).
   Placeholder mismatch stays in translator/critic validation (§6.98 translator path).
5. Chain exhausted → ``LLMRetryExhaustedError`` listing all slugs tried.
6. **``yandex_cloud``** unchanged — ``ModelChoice.chain`` from YAML.

**Implementation:** ``llm/client.py``, ``llm/retry.py`` (``should_advance_eliza_model_chain``),
``config/loader.py`` (``ElizaModelsConfig``), ``config/default.yaml``.

**Tests:** ``test_eliza_translate_chain_*``, ``test_eliza_429_after_retries_switches_*``,
``test_eliza_503_exhausted_switches_*``, ``test_eliza_4xx_does_not_advance_*``,
``test_eliza_parse_error_does_not_advance_*``, ``test_eliza_full_chain_429_raises_*``.

### 6.104. Cross-section scope overrun fix (#43997, 2026-07-15)

**Problem:** [#43997](https://github.com/ydb-platform/ydb/pull/43997) (20 RU recipe files) →
[#46577](https://github.com/ydb-platform/ydb/pull/46577) (36 EN files). Lateral BFS from
ancestor tocs (`recipes/toc_p.yaml`, `reference/toc_p.yaml`, `core/toc_i.yaml`) plus
**absent-EN full mirror** queued json-search, streaming-query, spring, sql-translation —
same mechanism as sqs-api under `reference/toc_p.yaml` (§6.92 partial fix).

**Decision:**

1. **Remove cross-section absent-EN mirror** from `_pages_from_discovered_toc` — scope
   pages = diff + locale ``{% include %}`` closure + **new** toc hrefs when toc is in diff.
2. **Gate BFS** — `_discover_ru_tocs(..., diff_paths=…)` follows ``include.path`` only
   when child toc is in diff-nav or its directory subtree contains a diff file
   (`_toc_dir_contains_diff`).
3. **#45181 behavior change:** sqs-api no longer auto-pulled when only `topic.md` is in
   diff; sqs-api stays in scope when directly in diff ([#44820](https://github.com/ydb-platform/ydb/pull/44820)).

**Implementation:** ``navigation/scope_planner.py`` (`a628f95`).

**Tests:** ``case_43997`` fixture (exact 20 md, ``doc_from_main == ∅``),
``test_case_45181_does_not_pull_sibling_sqs_api``, tightened ``<=`` → ``==`` on 44457/44820.

### 6.105. Cyrillic in-page link fragments (#43997 MD051, 2026-07-15)

**Problem:** `vector-search.md` EN kept ``[Vector search](#векторный-поиск)`` while
heading became ``#vector-search`` → **MD051** / ``build-docs`` red.
``mirror_link_href`` skipped ``#`` hrefs; ``check_link_locale_in_en`` did not flag them.

**Decision:**

1. **`build_heading_anchor_map(source, target)`** in ``validation/yfm_anchor.py`` —
   map RU auto-slugs + explicit ``{#…}`` to EN (``diplodoc_auto_slug``, ``english_yfm_anchor``).
2. **`localize_links_in_document(..., source_doc=…)`** remaps ``#frag`` and ``path#frag``
   via anchor map before render; ``render_with_translations`` passes ``source_doc=base_doc``.
3. **`check_link_locale_in_en`** — issue ``Cyrillic anchor fragment in EN document`` for
   any in-page / relative fragment with Cyrillic (local MD051 gate).

**Not in scope:** ``{#connect-ydb}`` ASCII YFM links, Wikipedia RU slugs (existing heuristics).

**Implementation:** ``validation/link_locale.py``, ``validation/yfm_anchor.py``,
``harness/render.py`` (`7685056`).

**Tests:** ``test_yfm_anchor.py``, ``test_link_locale.py`` (remap + validator).

### 6.106. ``doc_verify`` RU authority for merged source PR (#43997 → #46609, 2026-07-15)

**Problem:** [#43997](https://github.com/ydb-platform/ydb/pull/43997) merged 2026-07-12;
translation [#46609](https://github.com/ydb-platform/ydb/pull/46609) ran 2026-07-15.
``doc_translate`` bases the branch on ``main`` (§6.23) and copies fenced code from
checkout RU (post-merge snippets: ``.bulk_upsert``, ``.retry_tx``, ``use ydb::TxMode``).
``doc_verify`` still fetched RU at **source PR head** (§6.31) — stale pre-squash
snippets with the **same segment count** as EN → ~8 false ``fence_body_copy`` 🟡 on
``bulk-upsert``, ``retry``, ``tx-control``, ``vector-search``.

**Decision:**

1. **`source_pr_content_ref`** — merged source PR → upstream **``merge_commit_sha``**,
   not feature ``head.sha`` (aligns API RU with ``main`` / translate checkout).
2. **`pick_verify_ru_text``** — when API and local RU both match EN segment count but
   differ in content, prefer the variant with fewer ``fence_body_copy`` warnings;
   tie-break merged PRs toward **checkout RU**.

**Not fixed here:** real residual issues on #46609 (Wikipedia slug, trailing ``ы`` in
fence comment, placeholder critic noise) — separate tracks.

**Implementation:** ``github/pr.py`` (`source_pr_content_ref_from_pull``,
``pick_verify_ru_text(..., source_pr_merged=…)``).

**Tests:** ``test_github_pr_verify.py`` (merge commit ref, fence-body tie-break #43997).

### 6.107. Glossary profile + YFM003 variant A (#44457 → #46620, 2026-07-15)

**Problem:** [#44457](https://github.com/ydb-platform/ydb/pull/44457) translation
[#46620](https://github.com/ydb-platform/ydb/pull/46620) — ``glossary.md`` ~900 lines
re-translated with generic prompts → terminology critic noise, Wikipedia RU slugs on
``en.wikipedia.org``, ``build-docs`` 🔴 **16× YFM003** (internal links from glossary hub
to pages **not in EN toc graph**, e.g. ``spring-retry``, ``streaming-query``).

**Decision (product):** **Variant A — do not expand translation scope.** Strip or
de-link internal ``.md`` targets outside the EN sidebar graph during glossary finalize.
When those target pages are translated later, restore links in a follow-up PR.

**Glossary profile:** auto-detect ``concepts/glossary.md`` → dedicated system/translate/critic
prompts (English-only bold terms; critic skips “missing RU term in bold list”).

**Wikipedia:** keep RU ``ru.wikipedia.org`` URLs in source; finalize resolves EN article
via MediaWiki **langlinks**, then **Wikidata sitelinks** fallback — no domain swap with
Cyrillic slug.

**TOC reachability (variant A):**

1. BFS EN ``toc_p.yaml`` graph (+ pending EN ``.md`` / toc yaml from current PR plan).
2. ``strip_unreachable_glossary_links`` in ``finalize_en_target`` — internal ``.md`` link
   → plain anchor text when target ∉ reachable set.

**Implementation:** ``translation/file_profiles.py``, ``prompts/v1/*glossary*``,
``validation/glossary_toc_links.py``, ``validation/wikipedia_links.py`` (Wikidata),
``github/workflow.py`` (precompute ``en_toc_reachable`` before translate).

**Tests:** ``test_file_profiles.py``, ``test_glossary_prompts.py``,
``test_glossary_toc_links.py``, ``test_wikipedia_links.py``.

**Re-run:** delete ``ydbdoc-review/pr-44457``, bump ``v0.1.0``, label **doc_translate**
on [#44457](https://github.com/ydb-platform/ydb/pull/44457).

### 6.108. EN-only toc reachability for link strip (#44457 → #46637, 2026-07-15)

**Problem:** [#46637](https://github.com/ydb-platform/ydb/pull/46637) still had glossary /
``query_execution/index.md`` links to ``./streaming-query/watermarks.md``,
``spring-retry.md``, etc. → ``build-docs`` 🔴 ``unreachable-link … watermarks.html``.

**Root causes:**

1. ``collect_en_toc_reachable_md`` fell back to **RU toc yaml** whenever EN child toc
   was missing → BFS pulled RU-only pages (``streaming-query/watermarks.md``) into the
   reachable set → strip kept those links.
2. Strip ran **only on glossary** (`is_glossary_file`), not on other scoped md files.
3. ``if not reachable: return text`` and ``and en_toc_reachable`` skipped strip when
   the set was empty or unset.

**Fix:**

1. EN toc BFS reads **EN yaml only**; RU mirror allowed **only** for ``pending_en_tocs``
   from the current PR nav plan (new sidebars not yet on disk).
2. ``strip_unreachable_internal_links`` runs on **every** EN finalize when
   ``en_toc_reachable is not None``; resolve hrefs via ``en_mirror_path(file_path)``.
3. Tests: RU fallback must not add ``watermarks.md``; case_44457 watermarks/spring-retry strip.

### 6.109. Existence filter + Docker stale-image guard (#44457 → #46649, 2026-07-15)

**Problem:** [#46649](https://github.com/ydb-platform/ydb/pull/46649) still had
``json-indexes.md``, ``watermarks.md``, ``spring-retry.md`` in glossary. CI commit
message showed ``ydbdoc-review @ e9ff4e7`` (June) — **stale GHCR fallback** after
local Docker build failed silently.

**Fix:**

1. ``collect_en_toc_reachable_md``: add toc ``href`` to reachable only when the EN
   ``.md`` **exists on disk** (Diplodoc YFM003); ``pending_en_md`` paths always included.
2. ``action-docker.sh``: derive ``YDBDOC_GIT_SHA`` from Action checkout HEAD; pass into
   container; **disable GHCR fallback by default** (``YDBDOC_GHCR_FALLBACK=1`` to opt in);
   log which image is used.
3. ``finalize_en_target``: log stripped href count; ``workflow.py`` logs reachable set size.

**Ops:** publish GHCR via ``docker-publish`` workflow on tag ``v0.1.0`` after each release;
re-run **doc_translate** on #44457 — commit must show new SHA, not ``e9ff4e7``.

### 6.110. ``doc_verify`` RU candidates: head + merge + checkout (#46674, 2026-07-15)

**Problem:** [#46674](https://github.com/ydb-platform/ydb/pull/46674) (source
[#44457](https://github.com/ydb-platform/ydb/pull/44457) already merged). §6.106 made
``source_pr_content_ref`` return **``merge_commit_sha``** for merged PRs (449 segments
in ``glossary.md``). ``doc_translate`` still checked out **PR head** (443 segments) →
EN matched head → false 🔴 ``segment count mismatch: 449 vs 443``. Wikipedia DDL left
on ``ru.wikipedia.org`` when MediaWiki/Wikidata lookup failed in the runner.

**Decision:**

1. Primary API RU = source PR **head** again (§6.31 / translate checkout).
2. For merged PRs also fetch **merge commit** as ``ru_merge``; ``pick_verify_ru_text``
   chooses among head / merge / local by EN segment parity, then fewer
   ``fence_body_copy`` warnings (§6.106 still covers #46609).
3. Offline Wikipedia title map for common DDL/DML RU articles when live lookup fails.

**Tests:** ``test_github_pr_verify.py`` (#46674 head-over-merge), wikipedia offline map.

### 6.111. EN toc baseline = current upstream main (#39856 → #46845, 2026-07-16)

**Problem:** [#46845](https://github.com/ydb-platform/ydb/pull/46845) (translate of
[#39856](https://github.com/ydb-platform/ydb/pull/39856)) overwrote
``dev/streaming-query/toc_i.yaml`` and ``concepts/query_execution/toc_i.yaml``,
**dropping** EN-only / newer-on-main entries:

- ``local-and-external-topics.md`` (EN-only; other EN pages already link to it)
- ``execution_process.md`` (added on EN main after the source PR merge-base)

``build-docs`` → YFM003 ``File is not declared in toc`` on glossary, recipes,
``select/streaming.md``, plus hub links inside translated pages.

**Root cause:** ``_read_navigation_baselines`` preferred EN at **PR merge-base**.
Long-lived source PRs have a stale merge-base whose EN toc predates entries
added later on ``main``. Merge then had nothing to preserve/append.

**Fix:** Read ``en_main`` from ``merge_base_with`` (current upstream ``main``)
first; use merge-base EN only if absent on main. ``ru_base`` stays at merge-base
for scope. Preserve rule (§6.17 #5) unchanged once the baseline is current.

**Tests:** ``test_read_navigation_baselines_prefers_upstream_en_main``,
``test_merge_preserves_en_only_href_present_on_current_main``,
``test_merge_en_toc_preserves_en_only_local_and_external_topics``.

### 6.112. Wire ``en_toc_reachable`` into pair harness + keep existing EN toc (#39856 → #46846, 2026-07-16)

**Problem:** [#46846](https://github.com/ydb-platform/ydb/pull/46846) still failed
``build-docs`` with YFM003 on ``watermarks`` / ``concepts/streaming-query/…`` inside
**translated** pages, and again dropped ``local-and-external-topics`` from
``streaming-query/toc_i.yaml``. Reachability log showed 769 paths — strip never ran.

**Root causes:**

1. ``ExecutePairPlansStep`` built a parent ``HarnessContext`` with
   ``en_toc_reachable``, but ``run_pair_plan`` **rebuilt** ``HarnessContext``
   without forwarding it → ``finalize_en_target`` skipped strip
   (``en_toc_reachable is None``). Present since strip was introduced (§6.107).
2. EN toc baseline still fragile (empty → full RU mirror); EN-only hrefs lost.

**Fix:**

1. ``run_pair_plan``: pass ``en_toc_reachable=ctx.en_toc_reachable``.
2. ``_read_navigation_baselines``: try several upstream ref forms + worktree
   fallback; warn when EN baseline is empty.
3. ``merge_en_toc_yaml(..., keep_en_hrefs=…)``: do not drop EN-main hrefs whose
   ``.md`` still exists on upstream main (§6.112), even if listed in
   ``ru_base_hrefs``.

**Tests:** ``test_run_pair_plan_forwards_en_toc_reachable_to_harness``,
``test_merge_en_toc_keep_en_hrefs_overrides_ru_base_drop``.

### 6.113. Strip walker: Table is header/rows/cells (#39856 translate crash, 2026-07-16)

**Problem:** Re-run after §6.112 crashed the whole ``doc_translate`` job:

``AttributeError: 'Table' object has no attribute 'children'`` in
``strip_unreachable_internal_links`` (file with a markdown table, e.g. topic docs).

**Root cause:** Strip AST walk assumed ``Table.children`` / ``TableRow.children``;
real model is ``Table.header`` + ``Table.rows``, ``TableRow.cells``.

**Fix:** Walk ``header``/``rows``/``cells``; ``finalize_en_target`` catches strip
exceptions so a walker bug cannot abort the PR job.

**Tests:** ``test_strip_unreachable_links_inside_table_cells``.

### 6.114. Strip ↔ verify alignment + image bang spacing (#39856 → #46848, 2026-07-17)

**Problem:** After §6.107–§6.113, ``build-docs`` on translation PRs went green, but
``doc_verify`` stayed 🔴:

1. **``md_link_parity`` / critic** treated intentionally stripped EN links
   (``watermarks.md``, streaming-query pages outside the EN toc graph, …) as
   missing-link blockers — strip runs only in ``finalize_en_target``, while
   verify compared RU source links to the stripped EN text.
2. **``doc_verify``** never received ``en_toc_reachable`` (only ``doc_translate``
   built it), so even a wired filter could not fire on the QA path.
3. **Broken images** in EN: LLM sometimes emitted ``! [alt](⟦S1⟧)`` (space after
   ``!``). The inline parser treated it as prose + link; percent-encoded
   ``⟦S⟧`` then survived as ``%E2%9F%A6S1%E2%9F%A7`` instead of a real ``src``.

**Fix:**

1. ``check_md_link_parity`` ignores basenames whose EN targets resolve outside
   ``en_toc_reachable`` (``md_link_basenames_outside_reachable``).
2. Critic filter drops “missing link …” issues that mention those basenames;
   ``HeuristicsStep`` / ``run_critic_loop`` pass ``source_file`` + reachable set.
3. ``run_doc_verify`` builds the same reachable set and forwards it into
   ``PRHarnessContext``.
4. ``fix_image_bang_spacing`` (``! [`` → ``![``) in reinsert + EN postprocess;
   reinsert also recovers ``InlineLink`` whose href is an image ``⟦S⟧``
   placeholder (including URL-encoded forms).

**Tests:** ``test_md_link_parity_ignores_links_outside_en_toc_reachable``,
``test_drop_intentionally_stripped_link_critic_issues``,
``test_translate_image_bang_space_and_encoded_placeholder``,
``test_fix_image_bang_spacing``.

### 6.115. Strip walker: YfmIf uses branches (#39856 → #46870, 2026-07-17)

**Problem:** After §6.114, ``build-docs`` stayed green but ``doc_verify`` was still
🔴. ``finalize_en_target`` logged
``strip_unreachable_links_failed: AttributeError: 'YfmIf' object has no
attribute 'children'`` — strip aborted for files with ``{% if %}`` (e.g.
``topic.md``), so unreachable links remained / QA still complained.

Separately, critic often reported only ``Missing link placeholder ⟦U1⟧`` without
the ``.md`` basename, so the §6.114 basename filter did not drop those issues
for intentionally stripped streaming-query links.

**Fix:** Walk ``YfmIf.branches[].children`` (and ``YfmTabs`` / ``YfmTab``
properly); resolve mentioned ``⟦U*⟧`` placeholders against the segment atom map
and drop issues whose href is outside ``en_toc_reachable``.

**Tests:** ``test_strip_unreachable_links_inside_yfm_if``,
``test_drop_missing_u_placeholder_for_stripped_href``.
### 6.116. Parent toc must merge when child sidebar is needed (#46569, 2026-07-19)

**Problem:** [#46569](https://github.com/ydb-platform/ydb/pull/46569) translated
``streaming-query/*.md``, ``json-search/*.md``, ``sql-translation/*.md`` and even
merged **child** ``toc_*.yaml``, but **parent** EN sidebars stayed on legacy flat
links:

- ``concepts/toc_i.yaml``: EN ``href: streaming-query.md`` vs RU
  ``href: streaming-query/index.md`` + ``include.path: streaming-query/toc_p.yaml``
- ``recipes/toc_p.yaml``: missing ``json-search`` include entirely
- ``integrations/toc_i.yaml``: flat ``sql-dialect-converter.md`` vs RU section include

**Root cause:** ``_nav_needed`` only checks whether a **diff page basename** appears
as a direct ``href`` in the sidebar. Parents that only list ``section/index.md`` +
``include.path`` never match ``streaming-query.md`` / ``watermarks.md`` → parent
not queued; child is.

**Decision:** after the first ``_nav_needed`` pass, ``_queue_parents_of_needed_nav``
walks discovered tocs: if RU has ``include.path`` to a child already in
``nav_ru_paths`` and EN lacks that include, queue the parent (``nav_from_main``).

**Tests:** ``test_case_46569_queues_parent_toc_that_includes_needed_child``.

### 6.117. Orphan translated pages must be reachable from EN toc (#46569, 2026-07-19)

**Problem:** Even after parent-queue (§6.116), a translated EN ``.md`` can still land
off the sidebar graph (stale branch, partial nav merge, manual edits). Inverse of
``missing_toc_target`` (toc → missing file): page exists but no toc ``href`` reaches it.

**Decision:** ``check_orphan_translated_pages`` / ``apply_orphan_toc_page_checks``
after toc-target checks in ``run_doc_verify`` (and inline verify after translate):

1. Collect translated EN ``.md`` targets (skip ``_includes/``).
2. BFS EN toc graph from ``{docs_root}/en/core/toc_p.yaml`` with
   ``collect_en_toc_reachable_md(..., seed_extra_md=False)`` — pending toc texts are
   readable, but **not** seeded into the queue (disconnected child toc does not
   count as reachability).
3. Blocking ``orphan_toc_page:`` on each unreachable page → file verdict 🔴.

**Tests:** ``test_check_orphan_translated_pages_*``,
``test_apply_orphan_toc_page_checks_blocks_file_verdict``.

### 6.118. Keep ``include_path`` on href+include toc entries (#47100, 2026-07-19)

**Problem:** [#47100](https://github.com/ydb-platform/ydb/pull/47100) (Spring from
#43010) had a correct EN ``integrations/toc_i.yaml`` with Spring
``href`` + ``include.path``, but ``doc_verify`` reported 🔴
``scope_not_applied: include.path 'spring/toc-spring.yaml' … missing``.

**Root cause:** ``parse_toc_items`` / ``_flatten_toc_nodes`` kept only ``href`` when
both were present; ``_toc_entry_labels`` never saw the include. ``toc_translate_scope``
also ``continue``d after href and skipped include-path diff.

**Decision:** emit both fields on section entries; scope both independently.

### 6.119. supplement_only must not expand to all RU−EN missing hrefs (#46878, 2026-07-19)

**Problem:** [#46878](https://github.com/ydb-platform/ydb/pull/46878) (json-search from
[#41271](https://github.com/ydb-platform/ydb/pull/41271)) queued parent
``concepts/toc_i.yaml`` as ``supplement_only``. ``_resolve_toc_merge_scope`` then
set ``translate_hrefs = ru_hrefs − en_hrefs``, pulling ``secondary_indexes.md``
(and other RU-only paths) into EN → ``missing_toc_target`` / ``unexpected_href``.
Defeated §6.72 even though ``restrict_gap_fill_to_scope=True``.

**Decision:** for present EN tocs, scope = ``toc_translate_scope`` ∪ planned
extras only — never ``ru_hrefs − en_hrefs``.

**Tests:** ``test_pr_46878_supplement_only_does_not_add_all_missing_ru_hrefs``.

### 6.120. Merged source PR: translate RU from merge commit (#47100, 2026-07-19)

**Problem:** [#47100](https://github.com/ydb-platform/ydb/pull/47100) (Spring from
merged [#43010](https://github.com/ydb-platform/ydb/pull/43010)) failed ``build-docs``
with **YFM010** ``unreachable-autotitle-anchor`` on
``en/concepts/glossary.md`` → ``query_execution/index.html#sessions``.

**Root cause:** CI checked out **PR head**. After squash/rebase, head still had
``[{#T}](query_execution/index.md#sessions)`` while the **merge commit** (and
``main``) already pointed at ``execution_process.md#sessions`` (#44457). Faithful
translate of stale head **regressed** EN that #46674 had fixed.

**Decision:**

1. ``doc_translate``: when the source PR is **merged**, read RU (docs + nav) from
   ``merge_commit_sha`` (fetch if needed), not feature ``head.sha``.
2. ``restore_autotitle_hrefs(..., force_exact=True)`` on ``translate_to_en`` — when
   ``{#T}`` counts match, copy RU hrefs exactly (belt against LLM sibling-path
   hallucinations).
3. Example workflow: checkout ``merge_commit_sha`` when ``pull_request.merged``.

**Not changed:** ``doc_verify`` still prefers head first among candidates (§6.110)
so EN from a head-based translate stays alignable; merge remains an alternate.

**Tests:** ``test_translate_ru_content_ref_*``, ``test_restore_force_exact_ru_to_en_sessions_href``.

### 6.121. RU/EN toc structure parity + EN toc orphans (#43753 leftovers, 2026-07-19)

**Problem:** After [#43753](https://github.com/ydb-platform/ydb/pull/43753) translated
OTel recipe pages and [#43530](https://github.com/ydb-platform/ydb/pull/43530) moved
observability to ``reference/``, EN still had
``recipes/ydb-sdk/debug-otel-metrics.md`` /
``debug-otel-tracing.md`` **on disk** but **not in the recipes toc**.
[#45103](https://github.com/ydb-platform/ydb/pull/45103) had re-added a Troubleshooting
menu with the old ``debug-otel.md`` while RU no longer listed those recipes.

**Invariant:** RU and EN **sidebar structures must match** — same relative ``href``
and ``include.path`` sets for each toc pair. An EN ``.md`` that is not reachable
from ``en/core/toc_p.yaml`` is an orphan: delete it or wire it into toc (do not
leave unreachable translations). Prefer delete when ``redirects.yaml`` already
maps the old URL to the new section.

**Decision:**

1. ``toc_structure_parity`` (blocking) — RU vs EN href/include sets differ on
   entries that are not “EN-main legacy”.
2. ``toc_en_only_legacy`` (warning) — EN-only entries already present on EN main
   (§6.111 preserve); nudge toward RU mirror or drop.
3. Cleanup PR: [#47107](https://github.com/ydb-platform/ydb/pull/47107) deletes the
   orphan OTel recipe pages.
4. Repo-wide audit: ``scripts/find_toc_orphans.py``
   (``find_pages_missing_from_toc`` / ``find_en_pages_missing_from_toc``).

**Tests:** ``test_pr_43753_toc_structure_parity_*``, ``test_toc_en_only_legacy_*``.

### 6.122. Never leave bare ``{#T}`` after strip; EN toc graph from main (#47108, 2026-07-20)

**Problem:** [#47108](https://github.com/ydb-platform/ydb/pull/47108) (re-translate of
#43010) had ``glossary`` Sessions as ``section {#T}.`` — link markup gone.
``doc_verify`` 🔴 (``md_link_parity`` + critic formatting); critic fix with
``⟦U1⟧`` was rejected by placeholder protection.

**Root cause:** ``strip_unreachable_internal_links`` removed
``[{#T}](execution_process.md#sessions)`` because ``en_toc_reachable`` was built
from the **source PR checkout** (stale head without that toc entry), then left
the child text ``{#T}``.

**Decision:**

1. Build EN toc reachability from ``merge_base_with`` (upstream main) for EN
   paths during ``doc_translate``.
2. When stripping an unreachable autotitle link, substitute the path **stem**
   (never a bare ``{#T}``).
3. ``restore_autotitle_hrefs(..., force_exact=True)`` re-attaches bare ``{#T}``
   using RU hrefs missing from EN.

**Tests:** ``test_restore_force_exact_repairs_bare_autotitle_after_strip``,
``test_strip_unreachable_autotitle_uses_stem_not_bare_t``.

### 6.123. Always merge toc when RU changed, even if EN also changed (#47104, 2026-07-20)

**Problem:** [YDBDOCS-2550](https://st.yandex-team.ru/YDBDOCS-2550) /
[#47104](https://github.com/ydb-platform/ydb/pull/47104) ← [#41271](https://github.com/ydb-platform/ydb/pull/41271):
translated ``dev/json-indexes.md`` was 🔴 ``orphan_toc_page``. Source PR edited
both ``ru/.../dev/toc_p.yaml`` (added JSON indexes) and ``en/.../dev/toc_p.yaml``
(only moved Hybrid search). §6.76 skipped ``run_navigation_merges`` whenever
``en_changed``, so the new RU ``href`` never reached EN toc.

**Decision:** Markdown bilingual skip (§6.76) stays. For **navigation YAML**,
``run_navigation_merges`` runs whenever ``ru_changed`` — including when EN toc
was also in the source PR. Merge still preserves out-of-scope EN ``name`` blocks
and EN-only legacy hrefs; authors' partial EN toc tweaks no longer block wiring
translated pages.

**Tests:** ``test_pr_41271_nav_merge_runs_when_both_ru_and_en_toc_changed``.

### 6.124. Scope-aware ``toc_structure_parity`` for only_ru (#47108, 2026-07-20)

**Problem:** [#47108](https://github.com/ydb-platform/ydb/pull/47108) (Spring ← #43010)
correctly added ``spring/`` to EN ``integrations/toc_i.yaml``, then ``doc_verify``
🔴 ``toc_structure_parity`` because RU also has ``sql-translation/`` while EN still
has legacy ``sql-dialect-converter.md`` — pre-existing drift unrelated to Spring.

**Decision:**

1. When ``translate_hrefs`` / ``translate_include_paths`` is non-empty, ``only_ru_*``
   counts toward ``toc_structure_parity`` **only if** the missing entry is in that
   scope (failed to apply this merge). Unscoped RU-only structure is ignored here.
2. Empty scope: see §6.126 (no full-menu ``only_ru`` audit).
3. ``toc_en_only_legacy`` remains a soft warning; soft-only nav verdict stays
   ``ok`` so merge recommendation can be 🟢.

**Tests:** ``test_pr_47108_spring_toc_parity_ignores_unscoped_sql_translation_drift``.

### 6.125. Force-exact autotitle restore on ``critic_only`` verify (#47104, 2026-07-20)

**Problem:** Manual Sessions href fix on [#47104](https://github.com/ydb-platform/ydb/pull/47104)
was reverted by the next ``doc_verify`` critic fixup commit — ``restore_autotitle_hrefs(..., force_exact=True)``
ran only for ``translate_to_en``, not ``critic_only``.

**Decision:** run force-exact restore for EN targets on ``critic_only`` as well; when
RU/EN ``[{#T}]`` counts differ, still remap unique ``#fragment`` twins.

**Tests:** ``test_restore_force_exact_fragment_when_link_counts_differ``.

### 6.126. Empty translate scope must not full-audit ``only_ru`` (#47104, 2026-07-20)

**Problem:** [#47104](https://github.com/ydb-platform/ydb/pull/47104) (json indexes ← #41271)
had green ``build-docs`` and green content files, but the QA report stayed 🔴:
parent ``concepts/toc_i.yaml`` / ``recipes/toc_p.yaml`` failed ``toc_structure_parity``
on pre-existing ``only_ru`` drift (``secondary_indexes.md``, ``streaming-query/…``,
``nfs-backup/…``) while the translate scope for those menus was **empty**.
§6.124 point 2 still ran a full-menu ``only_ru`` audit on empty scope.

**Decision:** when both ``translate_hrefs`` and ``translate_include_paths`` are empty,
treat ``only_ru_*`` as **out of scope** (do not emit ``toc_structure_parity``).
Still block on **new** EN-only entries (not on main). Soft ``toc_en_only_legacy``
covers EN leftovers already on main. Scoped merges keep §6.124: ``only_ru`` blocks
only for entries in that scope.

**Tests:** ``test_pr_47104_empty_scope_does_not_block_preexisting_only_ru``,
``test_pr_43753_toc_structure_parity_ru_en_menus_must_match`` (empty vs scoped).

### 6.127. Translate and critic must never share a model (2026-07-20)

**Problem:** Yandex Cloud defaults used the same primary for both roles
(``deepseek-v32``). Eliza chains crossed each other (``deepseek-v4-flash`` ↔
``gpt-oss-120b``), so a 429 failover put translator and critic on the same slug.

**Decision:**

1. **Defaults (disjoint chains):**
   - Yandex: translate ``deepseek-v32`` → ``yandexgpt-5-pro``; critic
     ``yandexgpt-5.1`` → ``yandexgpt-5-lite``.
   - Eliza: translate ``deepseek-v4-flash`` only; critic ``gpt-oss-120b`` only
     (no cross-role fallbacks — only two reliable internal models).
2. **Runtime:** ``ensure_disjoint_translate_critic_chains`` strips any model that
   appears in the other role's chain (keeps both primaries; rejects equal
   primaries / empty chain after strip).

**Tests:** ``test_role_chains.py``, ``test_eliza_strips_overlapping_*``.

---

### 6.128. Overlay main autotitle fragments onto merge-commit RU (#47104, 2026-07-20)

**Problem:** Re-translating merged [#41271](https://github.com/ydb-platform/ydb/pull/41271)
read RU glossary from the merge commit, which still had
``query_execution/index.md#sessions``. ``force_exact`` (§6.125) copied that stale
href into EN → YFM010 on [#47104](https://github.com/ydb-platform/ydb/pull/47104)
(``build-docs`` red). Main RU (after [#44457](https://github.com/ydb-platform/ydb/pull/44457))
already pointed at ``execution_process.md#sessions``.

**Decision:** when loading RU from ``ru_content_ref`` (merge commit), overlay unique
``#fragment`` ``[{#T}]`` hrefs from ``merge_base_with`` (usually ``origin/main``)
via ``overlay_autotitle_fragment_hrefs``. Body stays at the merge snapshot; fragment
targets follow post-merge main.

**Tests:** ``test_overlay_autotitle_fragment_hrefs_*``.

---

### 6.129. Expand offline Wikipedia RU→EN map (#47104, 2026-07-20)

**Problem:** ``doc_verify`` on [#47104](https://github.com/ydb-platform/ydb/pull/47104)
could not call MediaWiki (TLS), so RU Wikipedia URLs stayed in EN → blocking
``link_locale`` / 🔴 report.

**Decision:** extend ``_OFFLINE_EN_TITLES`` (and Cyrillic fragment remap) for common
glossary/json-index articles so locale rewrite works without the network.

---


### 6.130. Stabilize Wikipedia resolve chain (2026-07-20)

**Problem:** ``resolve_wikipedia_href`` returned the original RU href on API
failure, and offline map was tiny — EN docs kept ``ru.wikipedia.org`` (#47104).

**Decision:** chain MediaWiki langlink → Wikidata → offline map → ``None``;
expand ``_OFFLINE_EN_TITLES`` (~80 hand-curated); map/drop Cyrillic fragments
with WARNING; ``mirror_link_href`` does not naive-swap Wikipedia hosts on miss.

**Tests:** ``test_wikipedia_links.py``.

### 6.131. Additive TOC merge models (gradual refactor) (2026-07-20)

**Problem:** AGENT_TASKS Task 2 proposed a full TocItem AST cutover, which
conflicts with «do not change public APIs / do not delete old code».

**Decision:** add ``TocMergeScope``, ``TocEntryMapping``, ``TocMergeIssue`` in
``toc_models.py``; ``_en_covers_ru_href`` delegates to mappings; document merge
strategy on ``merge_en_toc_yaml``. Defer unified AST parser/renderer to a later
tranche.

**Tests:** ``test_toc_models.py``.

### 6.132. Differential (incremental) translation (2026-07-21)

**Problem:** ``doc_translate`` always full-rewrote EN from the RU AST (§6.30),
burning tokens and risking regression on unchanged prose
(``AGENT_TASK_DIFFERENTIAL_TRANSLATION.md``).

**Decision:** when existing EN + merge-base RU are available and change
magnitude is low, **seed** unchanged segment translations from EN (aligned to
base RU via ``align_translations_from_target``) and LLM-translate only
added/modified PR segments. Still **render from the current source AST**
(§6.30 structural parity). Fall back to **full** when:

- no / empty EN, incomplete EN (``len(EN)/len(RU) < min_ratio``),
- EN stale (optional last-commit age > N days),
- change magnitude > threshold (default 50%),
- EN cannot be aligned to base RU (segment count/structure),
- ``translation.differential_enabled=false`` /
  ``YDBDOC_TRANSLATION_DIFFERENTIAL_ENABLED=0``.

**Wiring:** ``translation/differential.py``; ``PairContent.ru_base_text``;
``FileRunState.base_source_text`` + ``existing_target_text`` on translate;
``TranslateStep`` logs ``differential_meta``.

**Tests:** ``test_differential_translation.py``.

### 6.133. Verify EN toc from translation tip; allow safe placeholder reorder (2026-07-21)

**Problem:** ``doc_verify`` on #47104 reported 🔴 ``scope_not_applied`` /
``orphan_toc_page`` for json_* pages while the same checkout already listed
those hrefs in EN tocs — false red. Critic fixes for ``nfs-backup`` (reorder
``⟦U⟧``/``⟦V⟧``) were rejected by ``strict_placeholder_order``.

**Decision:**

1. Navigation verify and orphan checks prefer ``git show HEAD:en_path`` over a
   dirty worktree; seed orphan BFS with translation-PR EN toc paths.
2. ``strict_placeholder_order`` allows same placeholder **multiset** with a
   different order (post §6.55 aligned ids); still rejects renumber/add/drop.

**Tests:** ``test_toc_targets.py`` (HEAD vs stale WT); ``test_critic.py``
(reorder allowed / renumber rejected).

### 6.134. ACL, daily ₽ quota (YDB), S3 transcripts, continue label (2026-07-21)

**Problem:** Anyone who can label a PR can burn Yandex Cloud LLM budget;
costs are only in PR comments; there is no way to continue a run with
human instructions (wiki URL, retranslate one file, …) using prior LLM
context.

**Decisions (locked with product owner):**

1. **ACL** — GitHub Actions **repository variable**
   ``YDBDOC_ALLOWED_ACTORS`` (comma-separated logins). Gate in Python
   (``github/gates.py``) for ``doc_translate`` / ``doc_verify`` /
   ``doc_continue``; workflow ``if`` may short-circuit. Actor =
   ``GITHUB_ACTOR`` / label sender. Deny → PR comment, job exits 0
   (not a red CI). GitHub Team + ``read:org`` — later; variable first.
2. **Daily quota** — sum of estimated ``cost_rub`` per **MSK calendar day**
   ≤ ``YDBDOC_DAILY_BUDGET_RUB`` (default **5000**). Change via Actions
   variable / env (no code change). Persist runs in **YDB** (YC). Gate
   before LLM work; record after run (incl. denied). Soft lock / global
   concurrency to reduce double-spend races.
3. **Transcripts** — full LLM request/response JSON via ``TranscriptStore``.
   **Default backend now: YDB** ``run_objects`` (§20.11) until Object Storage
   quota is raised; then ``YDBDOC_TRANSCRIPT_BACKEND=s3``. Retention **14 days**.
   PR comments mention retention. Expired continue → ``expired_context`` +
   user-facing fallback (§20.9).
4. **Follow-up** — no separate bot. Label **``doc_continue``** on the
   translation PR (same pattern as ``doc_verify``). Instructions from the
   latest comment matching ``/ydbdoc continue …``. Max **3** continue
   cycles per source PR. Continue loads parent context from the active
   transcript backend, injects user text into translate+critic prompts,
   writes new run + report, counts toward daily ₽ quota and ACL.

**Phases:** see roadmap **Phase K**. Each sub-item ships with unit tests.

**Out of scope for K:** GitHub Team ACL; separate comment bot;
per-user quotas; public S3 URLs in PR comments.

**YDB auth (2026-07-22):** serverless DB
``/ru-central1/b1g7gqj2vnq67gjseuva/etns0641qf73btm7j21k`` via
``grpcs://ydb.serverless.yandexcloud.net:2135``; credentials =
``ydb.iam.ServiceAccountCredentials`` from SA JSON key; dep ``ydb[yc]``;
CI secret ``YDB_SA_KEY``. Details: ops **§20.7**. Schema: **§20.8**.

**S3 TTL / expired continue (2026-07-22):** Object Storage lifecycle **14 days**.
PR comments mention the retention. If user runs ``doc_continue`` after transcripts
are gone → deny with clear comment (do **not** silently no-op): delete
translation branch + re-label ``doc_translate``, **or** fix manually and label
``doc_verify``. Details: **§20.9**.


[← Memory Bank index](../../MEMORY_BANK.md)
