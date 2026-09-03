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
- EN cannot be aligned to base RU (segment count/**kind** structure, §6.163),
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

### 6.135. ``doc_verify`` on bilingual source PRs (#47233, 2026-07-27)

**Problem:** Authors often land RU+EN in one PR (typo fixes, param renames).
Operators want to hang **``doc_verify``** on that source PR to check translation
completeness/quality. Previously verify assumed a translation PR: no
completeness gaps on author diffs, and ``parse_source_pr_from_text`` could
steal RU from an unrelated ``PR #N`` in the title. Fork checkouts also failed
under the 2026-07-20 ``actions/checkout`` guard (verify workflow lacked
``allow-unsafe-pr-checkout`` / ``merge_commit_sha``).

**Decision:**

1. **Targets:** ``doc_verify`` accepts (a) ``ydbdoc-review/pr-N`` translation
   PRs and (b) any other docs PR as a **bilingual/source** verify.
2. **Source discovery:** parse ``PR #N`` from title/body **only** on
   translation branches. Bilingual author PRs use ``source_pr=None`` and load
   both locales from checkout.
3. **Completeness:** on non-translation PRs, compute
   ``completeness_gaps(changes, result)`` over git∪API diff of the verified PR
   (same §6.76 bilingual exemption). RU-only docs/nav without EN in the same
   diff → 🔴.
4. **CI example:** ``ydb-github-doc-verify-on-label.yml`` uses
   ``merge_commit_sha`` when merged + ``allow-unsafe-pr-checkout: true`` (same
   as translate). Deploy the same to ``ydb`` ``.github/workflows/ydbdoc-verify.yml``.

**Tests:** ``test_run_doc_verify_bilingual_source_pr_*``.

### 6.136. Verify must finalize EN fence comments; reset fixup branch (#47233, 2026-07-27)

**Problem:** On bilingual ``doc_verify`` for #47233 the critic/heuristics correctly
flagged Cyrillic ``--`` comments inside EN YQL fences, but the fixup PR
(``ydbdoc-review/verify-47233`` / #47964) kept Russian comments
(``Fixed segments: 0``). Root cause: ``VERIFY_PROFILE`` ran critic → heuristics
without ``finalize_en_target``, so
``translate_cyrillic_fence_comments_with_client`` never ran unless the critic
applied segment ``suggested_text`` and re-rendered. Fence-comment issues are
usually heuristics, not applied segment fixes.

Also a second ``doc_verify`` re-run left the stale fixup branch/PR in place until
a successful push path deleted it.

**Decision:**

1. Insert **``FinalizeEnStep``** in ``VERIFY_PROFILE`` so
   ``finalize_en_target`` always runs on EN (``fence_reference_text`` = EN self
   on verify) and Cyrillic ``--`` / ``//`` / ``#`` comments are LLM-translated
   even when critic applied 0 segment fixes. Order vs heuristics: see **§6.137**
   (heuristics/verdict on incoming EN, then finalize for the fixup write).
2. At the **start** of ``run_doc_verify`` for non-translation PRs (and again
   before push), delete ``ydbdoc-review/verify-{N}`` via API. Deleting the head
   closes any open fixup PR. Operator does not need to delete the branch by hand
   before re-labeling ``doc_verify``.

**Tests:** ``test_verify_profile_translates_yql_trailing_cyrillic_comments``;
``test_run_doc_verify_deletes_stale_fixup_branch_at_start``.

### 6.137. Verify heuristics before finalize; report №N; checkout SHA (#47233, 2026-07-27)

**Problem:** Second ``doc_verify`` on #47233 (after §6.136) reported 🟢 and
«можно мержить», while the merge tip still had Cyrillic ``--`` comments in EN
YQL. Root cause: ``FinalizeEnStep`` ran **before** heuristics, so the working
tree was already English when heuristics/verdict ran. Report looked clean even
though the verified tip had been bad. Separately:

1. Title ``отчёт #1`` GitHub-autolinks to
   [PR #1](https://github.com/ydb-platform/ydb/pull/1) in the same repo.
2. ``ReportMeta.checkout_ref`` was ``git_head_sha`` **after**
   ``prepare_translation_branch_on_base``, i.e. main tip, not the PR content SHA.

**Decision:**

1. ``VERIFY_PROFILE`` order was briefly heuristics-before-finalize so dirty tip
   forced 🟡. **Reverted by §6.138** — verdict must follow finalize. Remaining
   from this decision: report titles use **``отчёт №{n}``**; capture
   ``verify_content_sha`` before prepare as ``checkout_ref``.
2. ~~(obsolete) heuristics before finalize~~ — see §6.138.
3. Capture ``verify_content_sha = git_head_sha(repo_path)`` at the start of
   verify (before prepare/push) and pass it as ``checkout_ref``.

**Tests:** ``test_verify_profile_translates_yql_trailing_cyrillic_comments``
(verdict ``warnings`` + English ``final_text``); report builder ``№``;
``test_run_doc_verify_posts_comment`` asserts Checkout SHA before prepare.

**Superseded in part by §6.138** (verdict must follow finalize, not precede it).

### 6.138. Verify verdict = post-finalize outcome (#47233, 2026-07-27)

**Problem:** After §6.137, ``doc_verify`` on #47233 correctly ran fence-comment
auto-fix and found **no diff vs main** (comments already English via #47949),
but the report stayed 🟡 because heuristics scored the dirty incoming tip.
Operator question: why yellow if everything is already fixed?

**Decision:** ``VERIFY_PROFILE`` order is ``critic_loop`` → **FinalizeEn →
heuristics → verdict**. Recommendation reflects the text we would commit (and
what already sits on the fixup base when the commit is empty). Dirty tip alone
must not yellow the report after a successful auto-fix. The earlier false 🟢
was missing FinalizeEn (§6.136), not heuristics-before-finalize.

Keep §6.137 report ``№`` and early content ``checkout_ref``.

**Tests:** ``test_verify_profile_translates_yql_trailing_cyrillic_comments``
expects verdict ``ok`` + English ``final_text``.

### 6.139. YfmIf fence walk + mixed inline/block toc (#48009, 2026-07-27)

**Problem:** Translation [#48009](https://github.com/ydb-platform/ydb/pull/48009)
(from [#30237](https://github.com/ydb-platform/ydb/pull/30237)) stayed 🔴:

1. ``group-by.md`` — Cyrillic ``--`` comments inside ``{% if %}``. Heuristics
   (text scan) flagged them; ``FinalizeEn`` / ``collect_code_blocks`` missed them
   because ``YfmIf`` stores body in ``branches``, not ``.children``.
2. ``with.md`` — ``orphan_toc_page``. RU ``select/toc_i.yaml`` is mostly inline
   ``- { name, href }`` but WITH is a multiline ``- name:`` / ``href:`` /
   ``include.path`` block. ``parse_toc_items`` returned **only** inline items,
   so ``with.md`` was invisible to ``_nav_needed`` and never merged into EN toc.
   Block parser also swallowed following inline siblings into the WITH ``block``.

**Decision:**

1. ``collect_code_blocks`` / ``_walk_blocks`` recurse into ``YfmIf.branches``
   (same pattern as link_locale / glossary strip).
2. ``parse_toc_items`` merges inline + block items in document order; block
   parser stops at the next ``- {`` list item so WITH's ``block`` stays clean.

**Tests:** ``test_collect_code_blocks_inside_yfm_if``,
``test_translate_cyrillic_fence_comments_inside_yfm_if``,
``test_pr_48009_mixed_inline_block_toc_parses_with_md``,
``test_pr_48009_md_only_queues_select_toc_when_en_missing_with``,
``test_pr_48009_merge_adds_with_into_inline_en_toc``.

### 6.140. EN nav baseline = translation-branch tip (#48018, 2026-07-28)

**Problem:** Re-translate of merged [#30237](https://github.com/ydb-platform/ydb/pull/30237)
→ [#48018](https://github.com/ydb-platform/ydb/pull/48018) still left
``with.md`` as ``orphan_toc_page`` after §6.139. At the source merge commit EN
``select/toc_i.yaml`` still listed ``with.md``; today's ``main`` tip does not
(WITH dropped by [#47995](https://github.com/ydb-platform/ydb/pull/47995)).

``make_repo_scope_readers`` used ``merge-base(HEAD, origin/main)`` for
``read_en_base``. For merged PRs HEAD is an ancestor of main → merge-base ==
HEAD → ``_nav_needed`` saw WITH on the stale EN toc and **did not queue**
``select/toc_i.yaml``. The translation branch is cut from **current** main
(no WITH) → orphan. Nav **merge** already preferred upstream tip (§6.111);
scope planning did not.

**Decision:**

1. ``read_en_base`` / orphan BFS use ``read_text_at_upstream_tip(merge_base_with)``
   (translation-branch tip), not merge-base HEAD.
2. ``doc_translate`` runs ``apply_orphan_toc_page_checks(..., baseline_ref=merge_base_with)``
   before commit; orphan paths join ``completeness_gaps`` and skip push.

**Tests:** ``test_pr_48018_scope_readers_use_upstream_en_tip_not_stale_merge_base``,
``test_orphan_check_uses_baseline_ref_not_stale_head``.

### 6.141. Nav merge no-op + honest source comment (#47856, 2026-07-28)

**Problem:** Merged [#47856](https://github.com/ydb-platform/ydb/pull/47856) only
reordered ``FROM Topic`` / ``topics.md`` in RU ``select/toc_i.yaml``. That href
is absent from EN toc (and ``topics.md`` has no EN page). Scoped merge (§6.82
``restrict_gap_fill_to_scope``) skipped the RU-only entry; shared EN order was
unchanged → empty ``git commit`` → no push → no translation PR. The source
comment still said **«перевод готов»** with ``Translation PR | —`` and
``Файлов | 1``, and spent ~₽0.91 translating a gap label that was never applied.

**Decision:**

1. After toc/redirect merge, if ``merged`` equals the EN baseline →
   ``target_text=None`` (no disk write, not counted as translated).
2. When ``restrict_gap_fill``, do not LLM-translate out-of-scope gap href labels.
3. ``build_source_pr_comment(..., committed=…)``: if no translation PR and
   ``committed is False`` (or zero files), say **«перевод не требуется»** with
   §6.141 explanation — never **«перевод готов»** without a PR.

**Tests:** ``test_pr_47856_ru_only_toc_reorder_is_nav_noop``,
``test_build_source_pr_comment_noop_no_commit``.

### 6.142. Repair missing EN ``path#fragment`` after translate (#48047, 2026-07-28)

**Problem:** ``build-docs`` on translation [#48047](https://github.com/ydb-platform/ydb/pull/48047)
failed with two ``Title not found`` WARNs that were already on ``main`` from prior
auto-translates:

1. ``glossary.md`` → ``query_execution/index.md#sessions`` — heading lives on
   ``execution_process.md``. Re-translate of merged [#41271](https://github.com/ydb-platform/ydb/pull/41271)
   → [#47995](https://github.com/ydb-platform/ydb/pull/47995) re-applied the stale
   merge-commit path despite §6.128 overlay.
2. ``create-resource-pool-classifier.md`` → ``authentication.md#ldap`` — RU
   ``{#ldap}`` vs EN ``{#ldap-auth-provider}``. ``force_exact`` copied the RU
   fragment; in-page heading maps do not cover **cross-file** targets.

**Decision:** after ``restore_autotitle_hrefs(..., force_exact=True)`` run
``repair_en_fragments`` (§6.142) with a docs reader (worktree, else upstream tip):

1. If EN target lacks ``{#frag}``, prefer EN-baseline or RU-source autotitle path
   that **does** declare the frag on the EN tree.
2. Else load RU/EN twins of the linked page and remap ``frag`` via
   ``build_heading_anchor_map`` (ldap → ldap-auth-provider).

Wire ``docs_text_reader`` through ``PRHarnessContext`` / ``HarnessContext`` from
``doc_translate`` / ``doc_verify`` workflows.

**Tests:** ``test_pr_48047_sessions_prefers_en_baseline_path``,
``test_pr_48047_sessions_uses_ru_overlay_path_when_en_declares``,
``test_pr_48047_ldap_remaps_via_heading_map``.

### 6.143. Forward ops env into Docker action (#48047 doc_continue, 2026-07-28)

**Problem:** Same-day ``doc_continue`` on
[#48047](https://github.com/ydb-platform/ydb/pull/48047) posted the **14-day TTL**
``expired_context`` comment. CI log:

``Transcript store unavailable (YDB SA key not configured…); using null store``

Workflow set ``YDB_SA_KEY`` and wrote ``YDBDOC_YDB_SA_KEY_FILE`` on the **runner**,
but ``action-docker.sh`` only forwarded a whitelist of env vars into the
container — **without** ``YDB_SA_KEY`` / ``YDBDOC_TRANSCRIPT_BACKEND`` / ACL
quota / ``GITHUB_ACTOR``. Host file paths are also invisible inside Docker
unless mounted. Translate on ``ydb`` often omitted ``YDB_SA_KEY`` entirely, so
transcripts were never written.

**Decision:**

1. ``action-docker.sh`` forwards ops secrets **by name** (``-e VAR``, safe for
   multiline JSON) including ``YDB_SA_KEY``, ``YDBDOC_TRANSCRIPT_BACKEND``,
   ``YDBDOC_ALLOWED_ACTORS``, ``YDBDOC_DAILY_BUDGET_RUB``, ``GITHUB_ACTOR``, …
2. If ``YDBDOC_YDB_SA_KEY_FILE`` exists on the host, mount it at
   ``/run/secrets/ydb-sa.json`` and set that path inside the container.
3. When the transcript store fails to start, ``doc_continue`` posts
   ``store_unavailable_comment`` (misconfig), **not** the TTL text.
4. Example / production ``doc_translate`` workflows must pass ``YDB_SA_KEY`` +
   ``YDBDOC_TRANSCRIPT_BACKEND=ydb`` so the parent run is persisted for continue.

**Tests:** ``test_action_docker_forwards_ydb_sa_key``,
``test_continue_store_unavailable_is_not_ttl_message``.

### 6.144. Nav merge no-op must satisfy completeness (#47091, 2026-07-28)

**Problem:** Re-translate of [#47091](https://github.com/ydb-platform/ydb/pull/47091)
after §6.143 skipped commit/push with completeness gap
``ydb/docs/en/core/yql/toc_i.yaml`` — «navigation merge не выполнен». Log showed
``Navigation merge no-op … EN unchanged vs upstream baseline`` (§6.141): merge
returned ``target_text=None``, ``verdict=ok``. ``committed_en_paths`` only
counted nav rows with ``target_text is not None``, so a successful no-op looked
like a missing mirror and blocked the translation PR.

**Decision:** ``committed_en_paths`` treats navigation results with
``verdict=ok`` and no ``error`` as satisfied even when ``target_text is None``
(intentional no-op / RU-deleted handled elsewhere). Real merge failures still
leave a gap.

**Tests:** ``test_completeness_ok_when_navigation_noop``.

### 6.145. Verify fixup comment must not say «ветка перевода» on bilingual PRs (#46742, 2026-07-28)

**Problem:** ``doc_verify`` on bilingual/author [#46742](https://github.com/ydb-platform/ydb/pull/46742)
opened fixup [#48045](https://github.com/ydb-platform/ydb/pull/48045) (base ``main``, correct
per §6.64 / ``verify_fixup_pr_base``) but posted
``build_verify_fixup_source_comment``: «Замёрджите его в ветку перевода…» —
wrong for non-translation PRs.

**Decision:** ``build_verify_fixup_source_comment(..., translation_pr=)`` —
translation PRs keep the old wording; bilingual/author PRs tell the operator to
merge the fixup into the verified base (usually ``main``) or cherry-pick onto
the author branch.

**Tests:** ``test_verify_fixup_comment_bilingual_vs_translation``.

### 6.146. Bilingual verify: QA report on fixup PR + ``doc_continue`` on ``verify-*`` (#46742, 2026-07-28)

**Problem:** After bilingual ``doc_verify``, the full QA report stayed on the
**source** PR while critic commits lived on ``ydbdoc-review/verify-N``. Operators
could not iterate with ``doc_continue`` on the fixup (only translation
``pr-N`` heads were treated as inline-push targets), and the source thread mixed
report + «merge fixup» noise.

**Desired UX:** bilingual author PR → ``doc_verify`` → either 🟢 on source, or a
fixup PR that carries the **full** mergeability report; if still 🔴, continue on
that fixup via ``/ydbdoc continue`` + ``doc_continue``.

**Decision:**

1. **Report routing:** when a new fixup PR is opened, post ``build_full_report``
   on the **fixup** issue; source gets only ``build_verify_fixup_source_comment``
   (pointer + merge hint + continue-on-fixup). No fixup → report stays on the
   verified PR (translation / bilingual green / no disk writes).
2. **Inline push on ``verify-*``:** ``is_verify_fixup_branch`` — same push path as
   translation heads (no second fixup PR; do not delete own branch on start).
3. **Scope on fixup re-verify/continue:** pairs/nav from the **original** source
   PR file list (``verify-N`` → source ``N``), content from the fixup checkout.
4. Examples / continue docstring mention ``verify-*``.

**Tests:** ``is_verify_fixup_branch`` in ``test_github_pr``; updated
``test_verify_fixup_comment_bilingual_vs_translation``.

### 6.147. Verify false 🔴: odd toc indent, self-links, YFM tables, realign (#46742, 2026-07-28)

**Problem:** bilingual ``doc_verify`` on [#46742](https://github.com/ydb-platform/ydb/pull/46742)
stayed 🔴 with mixed false positives and real gaps:

1. ``hive-booting.md`` orphan — page is in ``contributor/toc_i.yaml`` under Hive,
   but nested ``href`` used **odd indent** (5 spaces); tree parser required
   ``parent+2`` and skipped the child.
2. ``hive_config.md`` link parity — only missing basename was a **RU self-link**
   to ``hive_config.md``.
3. ``kafka_proxy_config`` alignment 5↔48 — RU ``#|`` YFM table was one paragraph;
   EN GFM table exploded into cells.
4. Real structure gaps (missing table rows / condensed ``auth.md``) could not be
   fixed by critic because alignment failure short-circuits the critic loop.

**Decision:**

1. Toc tree parse: accept any deeper indent for ``href`` / ``items:``; set child
   ``list_indent`` from the first nested ``- name:`` line.
2. ``md_link_parity``: drop the source file's own basename from the missing set.
3. YFM table plugin (``#|`` … ``|#``) → standard table tokens / ``Table`` IR;
   render as GFM (EN house style).
4. ``RoundTripStep`` on ``verify``: if alignment still fails, **rebuild EN from
   RU** (full ``translate_segments`` + render), then re-gate — so critic can
   finish and ``doc_continue`` can converge to 🟢. The ``verify_realign:`` note
   is classified as **info** (not blocking).

**Tests:** ``test_collect_toc_link_targets_odd_nested_indent``,
``test_md_link_parity_ignores_self_basename_link``, ``test_yfm_tables``,
``test_verify_realign_message_is_info_not_blocking``.

### 6.148. RU→EN ``{% include %}`` parity + auto-repair (#48103 / career, 2026-07-28)

**Problem:** ``{% include %}`` is not a translation segment. RU
``hive-booting.md`` ended with ``{% include [career](./_includes/career.md) %}``
while EN lacked the call (EN ``_includes/career.md`` already existed). Segment
align + critic stayed green — silent footer/CTA loss. Post-merge auto-translate
would accumulate such holes forever.

**Decision:**

1. Blocking heuristic ``include_parity``: every locale-relative include in RU
   must appear in EN (match by resolved EN mirror path).
2. Auto-repair in finalize / PR safety net: append missing include lines when
   the EN target file exists; leftover gaps stay blocking.
3. ``include_parity_repaired:`` is **info** (not red).

**Tests:** ``test_include_parity_detects_missing_career_include``.

### 6.149. Fence QA: trailing YAML ``#`` + angle placeholders (#47164, 2026-07-28)

**Problem:** Bilingual ``doc_verify`` on #47164 left ``host_configs.md`` 🟡 with
``fence_body_copy`` on blocks that only differed by (1) trailing YAML
``# необязательный`` → ``# optional`` and (2) RU ``<имя домена>`` /
``<тип…>`` → EN ``<domain name>`` / ``<type…>``. Trailing ``#`` was not
recognized as a comment (only full-line ``#`` / trailing ``//`` / ``--``).
Angle placeholder wording outside the fixed map failed compare even though
structure matched. On verify, ``fence_ref=EN`` so enforce from RU is a no-op —
false 🟡 blocked green without a real code corruption.

**Decision:**

1. Treat trailing ``\s+#\s*`` like trailing ``//`` / ``--`` in fence comment
   collect / translate / ``comment_translation_only`` compare.
2. Collapse RU→EN ``<…>`` placeholder pairs (Cyrillic inner in source, Latin
   in target, same slot count per line) before fence structural compare;
   allow that alone or combined with comment translation.
3. Extend the angle-placeholder repair map for common host_configs phrases
   (translate path), without requiring exact map hits for QA match.

**Tests:** ``test_fence_content_allows_trailing_hash_yaml_comment_translation``,
``test_fence_content_allows_angle_placeholder_translation``,
``test_fence_content_allows_angle_placeholder_plus_hash_comment``.

### 6.150. Mirror RU toc menu reshuffles into EN (#47856, 2026-07-28)

**Problem:** [#47856](https://github.com/ydb-platform/ydb/pull/47856) only moved
``FROM Topic`` / ``topics.md`` in RU ``select/toc_i.yaml``. At merge time EN
lacked the page → §6.141 no-op. Later EN gained ``topics.md`` (still at the old
position). Operators expect a RU menu reshuffle to land in EN at the same
relative place without a manual EN toc edit.

**Decision:**

1. ``merge_en_toc_yaml`` already walks RU PR order — shared EN blocks are
   emitted at the RU position (no separate permute pass).
2. ``toc_reordered_shared_hrefs(ru_base, ru_pr)`` detects when the shared href
   subsequence changed (reorder-only; pure adds do not count).
3. When gap-fill is restricted (§6.82) and a reordered href is **missing from
   EN toc** but the EN ``.md`` exists on upstream tip → add that href to
   translate scope so gap-fill inserts it at the RU position.
4. RU-only reshuffles with **no** EN page remain a no-op (§6.141).

**Tests:** ``test_toc_reordered_shared_hrefs_detects_move``,
``test_merge_mirrors_ru_shared_href_reorder``,
``test_pr_47856_shared_toc_reorder_mirrors_en_order``,
``test_pr_47856_reorder_adds_en_page_missing_from_toc``,
``test_pr_47856_ru_only_toc_reorder_is_nav_noop``.

### 6.151. Nav-only QA report is 🟢 (#47856 / #48124, 2026-07-28)

**Problem:** Toc-only translate (#47856 → #48124) correctly moved ``FROM Topic``
in EN, but the QA header said **⚪ нет обработанных файлов** because
``_merge_recommendation`` only counted markdown ``pair_results`` toward 🟢.
Navigation ``verdict=ok`` (including soft ``toc_en_only_legacy``) was ignored.

**Decision:** if there are no blocking/warning pair or nav results, and at least
one navigation result is ``ok``, recommend **🟢 можно мержить**.

**Tests:** ``test_merge_recommendation_green_for_nav_only_ok``.

### 6.152. ``strip_unreachable_links`` is info, not 🔴 (#46889 / #48123, 2026-07-28)

**Problem:** Bilingual ``doc_verify`` on [#46889](https://github.com/ydb-platform/ydb/pull/46889)
realigned ``glossary.md`` from RU (§6.147), then Variant A
``strip_unreachable_internal_links`` (§6.107) correctly removed 5 internal hrefs
outside the EN toc graph (e.g. ``streaming-query.md``, watermarks). The strip
appended ``strip_unreachable_links: removed N…`` to finalize warnings, and
``_classify_heuristic`` defaulted that prefix to **blocking** → fixup [#48123](https://github.com/ydb-platform/ydb/pull/48123)
stayed 🔴 despite a successful intentional repair (same class of bug as
``verify_realign`` before §6.147).

**Decision:**

1. ``strip_unreachable_links:`` → **info** (shown under «Справка»).
2. ``strip_unreachable_links_failed:`` → **warnings** (walker/exception path).

**Tests:** ``test_strip_unreachable_links_message_is_info_not_blocking``.

### 6.153. Fragment repair when RU and EN baseline are both stale (#48012, 2026-07-28)

**Problem:** [#48012](https://github.com/ydb-platform/ydb/pull/48012) ``docs_build``
failed with Title not found on:

* ``glossary.md`` → ``query_execution/index.md#sessions``
* ``create-resource-pool-classifier.md`` → ``authentication.md#ldap``

Those pages were **not** in the PR diff (only ``limits-ydb.md``). The PR head was
~69 commits behind ``main`` and still carried broken fragments from our
auto-translates [#47995](https://github.com/ydb-platform/ydb/pull/47995) /
[#47949](https://github.com/ydb-platform/ydb/pull/47949) (before §6.142). ``main``
already had the correct links (fixed in [#46889](https://github.com/ydb-platform/ydb/pull/46889)).

§6.142 repair could not retarget ``sessions`` when **both** RU source and EN
baseline still pointed at ``index.md#sessions`` — no path declared ``{#sessions}``.

**Decision:**

1. After baseline/RU/heading-map steps, search sibling ``href``s from the local
   ``toc_*.yaml`` (and ``index.md`` → ``execution_process.md`` hint) for a page
   that declares ``{#frag}``; rewrite the link.
2. Unblock author PRs stuck on stale bases: update-branch onto ``main`` when
   maintainer-can-modify (done for #48012).

**Tests:** ``test_pr_48012_sessions_finds_sibling_when_ru_and_en_baseline_stale``.

### 6.154. Verify include_parity vs merge-commit RU; empty include scope (#38700, 2026-07-28)

**Problem:** Auto-translate of merged [#38700](https://github.com/ydb-platform/ydb/pull/38700)
produced [#48133](https://github.com/ydb-platform/ydb/pull/48133) with 🔴
``include_parity`` / ``include_target``:

1. ``import-resource-broker-note.md`` — added to RU *after* #38700 merge
   (#45064). ``doc_translate`` correctly read RU from the merge commit (§6.120),
   but ``doc_verify`` / ``apply_include_parity_repair`` compared EN to checkout
   ``main`` (or picked ``ru_local`` on equal segment+fence scores).
   ``{% include %}`` is not a segment, so main RU matched EN segment count while
   carrying post-merge includes.
2. ``options_overlay.md`` — empty (size 0) RU include referenced from
   ``_includes/index.md``. Scope closure used ``if read_ru(target):``, so the
   empty file never entered ``doc_from_main`` and EN never got a mirror.

**Decision:**

1. ``pick_verify_ru_text``: on equal fence score for a merged source PR, prefer
   ``ru_merge`` over checkout/main (still lose to fewer ``fence_body_copy``).
2. ``apply_include_parity_repair``: use ``PairRunResult.source_text`` (the body
   actually verified/translated), not disk checkout.
3. Scope include closure: ``read_ru(target) is not None`` so empty RU includes
   are queued; empty → empty EN via existing no-segment ParseStep path.

**Tests:** ``test_pick_verify_ru_text_merged_prefers_merge_over_main_when_fence_equal``,
``test_apply_include_parity_repair_uses_pair_source_text_not_checkout``,
``test_scope_closes_empty_locale_include_missing_on_en``.

### 6.155. Section href+include merge + absent-EN toc page queue (#46446, 2026-07-29)

**Problem:** Auto-translate of merged [#46446](https://github.com/ydb-platform/ydb/pull/46446)
(only ``watermarks.md`` ×2) produced [#48183](https://github.com/ydb-platform/ydb/pull/48183) 🔴:

1. EN ``concepts/streaming-query/toc_i.yaml`` full-mirrored from RU listed
   ``streaming-query.md``, but that page was never queued → ``missing_toc_target``.
2. Parent ``concepts/toc_i.yaml`` was queued (§6.116) with planned
   ``include.path: streaming-query/toc_p.yaml``, yet merge kept EN's flat
   ``href: streaming-query.md`` and never emitted the RU section
   (``href: streaming-query/index.md`` + include) → ``scope_not_applied`` /
   ``toc_structure_parity``.

**Root cause:** flat ``merge_en_toc_yaml`` handled ``if href: … continue`` before
``include_path``, so a scoped include on a section whose href differs from the
EN legacy flat path was skipped. Planner queued the absent child toc (§6.85)
without pulling sibling/section ``href`` pages into ``doc_from_main``.

**Decision:**

1. Treat href+include as a section entry: if ``include_path`` ∈ translate scope
   (or href is), emit the full RU block even when EN still has a flat alias.
2. After nav queue: for **sibling** absent EN tocs (same directory as a diff
   page), add every RU toc ``href`` missing on EN; for parent items whose
   ``include.path`` child is that sibling sidebar, also queue the section
   ``href`` (e.g. ``streaming-query/index.md``). Do not expand ancestor hubs
   (``reference/toc_p``) — that reopens §6.104. Re-run include closure.

**Tests:** ``test_merge_applies_scoped_include_when_section_href_differs_from_en_flat``,
``test_pr_46446_absent_en_streaming_toc_queues_sibling_pages``.

### 6.156. Heading AST parity; strip vs md_link_parity; fence trailing blank (#30237, 2026-07-29)

**Problem:** Auto-translate [#30237](https://github.com/ydb-platform/ydb/pull/30237)
→ [#48202](https://github.com/ydb-platform/ydb/pull/48202) 🔴:

1. ``group-by.md`` ``heading_parity`` 24 vs 25 — RU ``## ROLLUP`` is indented
   inside ``{% if feature_group_by_rollup_cube %}``; line-regex ``^#{1,6}``
   missed it while EN heading is flush-left.
2. Same file ``fence_body_copy`` on block 4 — only ``--`` comment translation
   plus a trailing blank line in RU (13 vs 12 lines) failed the equal-length
   gate of comment-only compare.
3. ``system-views.md`` ``md_link_parity`` for ``table.md`` /
   ``create-resource-pool-classifier.md`` after finalize
   ``strip_unreachable_links`` removed them — verify-time reachability no
   longer treated those basenames as ignorable.

**Decision:**

1. ``check_heading_parity`` counts headings via AST, walking ``YfmIf.branches``.
2. ``_fence_diff_is_comment_translation_only`` drops trailing blank lines before
   comparing.
3. Strip info lists removed ``*.md`` basenames; ``HeuristicsStep`` passes them
   to ``md_link_parity`` as ``ignore_basenames``.

**Tests:** ``test_heading_parity_counts_indented_headings_inside_yfm_if``,
``test_md_link_parity_ignores_stripped_basenames``,
``test_fence_body_allows_comment_translation_with_trailing_blank_line``.

### 6.157. Copy locale ``_assets`` binaries RU→EN (#45185 / #48187, 2026-07-30)

**Problem:** Auto-translate [#45185](https://github.com/ydb-platform/ydb/pull/45185)
→ [#48187](https://github.com/ydb-platform/ydb/pull/48187) QA 🟢 but
``build-docs`` failed:

```text
ENOENT … en/contributor/_assets/major_release_branches.svg
```

Source PR edited ``manage-releases.md`` (versioning note). RU already linked
``![…](_assets/major_release_branches.svg)``; full-file translate correctly
brought that image into EN. The SVG existed only under ``docs/ru/…/_assets/``
(EN never had this diagram file). Pipeline wrote only ``.md`` / nav YAML —
no binary copy — so Diplodoc resolved the EN-relative asset to a missing path.

This is **not** scope pollution: ``manage-releases.md`` was in the source PR.
The diagram is part of that page’s release-branch scheme section.

**Decision:**

1. New ``validation/locale_assets.py``: from RU ``source_text``, collect relative
   image hrefs with binary extensions; resolve under ``docs/ru``; map via
   ``counterpart`` + strip ``-rub`` (§6.47) for the EN path; ``shutil.copy2``
   when EN is missing or differs.
2. ``_apply_results_to_disk`` runs asset copy for every successful pair — so
   both ``doc_translate`` and ``doc_verify`` heal missing EN assets without a
   full retranslate.
3. Re-run after fix: prefer **``doc_verify``** on the translation PR (cheaper);
   ``doc_translate`` only if EN markdown must be regenerated.

**Tests:** ``test_locale_assets.py`` (plan + rub strip + copy idempotence).

### 6.158. ``repair_en_fragments`` must not emit unreachable bare basenames (#48223 / #48272, 2026-07-30)

**Problem:** After [#48223](https://github.com/ydb-platform/ydb/pull/48223)
merged, bilingual [#48272](https://github.com/ydb-platform/ydb/pull/48272)
``build-docs`` failed:

```text
Link is unreachable: en/dev/create-resource-pool.md in en/dev/system-views.md
Link is unreachable: en/dev/topic.md in en/dev/system-views.md
```

Root cause: ``doc_verify`` on #48223 ran ``repair_en_fragments`` on correct
links from a manual fix:

- ``../concepts/datamodel/table.md#partitioning`` (stub page + include; no
  exact ``{#partitioning}``)
- ``../yql/reference/syntax/create-resource-pool-classifier.md#parameters``
  (``### Parameters`` auto-slug, no explicit ``{#parameters}``)

Repair treated fragments as missing, searched the **target folder** toc, found
``topic.md`` / ``create-resource-pool.md``, then on ``PurePosixPath.relative_to``
failure fell back to the toc's bare ``href``. Those basenames resolve under
``en/core/dev/`` (the linking page) → unreachable.

**Decision:**

1. Always emit toc-sibling hits as a path **relative to the linking page**
   (posix ``..`` climb) — never the toc-local basename.
2. Treat Diplodoc auto-slugs and locale ``{% include %}`` bodies as declaring
   fragments.
3. Same-page unique prefix remap (``#partitioning`` → ``#partitioning_row_table``)
   before toc retarget.

**Tests:** ``test_pr_48223_does_not_mangle_existing_targets_to_bare_basenames``,
``test_fragment_declared_accepts_diplodoc_auto_slug``.

### 6.159. Restore dispatch-only ``rebuild_docs`` workflow (#48223 / #48410, 2026-07-30)

**Problem:** After [#48223](https://github.com/ydb-platform/ydb/pull/48223)
merged, translation PRs got a red **Rebuild documentation** check and a
cancelled **Build documentation** even when QA was 🟢 (e.g. #48409).

Root cause: #48223 replaced ``.github/workflows/docs_build_rebuild.yaml`` with
a hybrid that (1) runs ``docs-build-action`` **without checkout** → ENOENT
``ydb/docs``, and (2) only ``workflow_dispatch``es ``docs_build.yaml`` from a
misnamed final step. ``docs_build.yaml`` concurrency
``docs-build-<PR>`` + ``cancel-in-progress: true`` then cancels the in-flight
``pull_request`` build.

**Decision:** Keep ``trigger-translation-ci`` adding ``rebuild_docs`` +
``ok-to-test`` (``GITHUB_TOKEN`` does not cascade CI; see §16.7). Restore the
[#43222](https://github.com/ydb-platform/ydb/pull/43222) rebuild workflow:
remove label → check ``ydb/docs/**`` → ``createWorkflowDispatch(docs_build.yaml)``
only. No local Diplodoc build in privileged ``pull_request_target``.

**Fix PR:** [ydb #48410](https://github.com/ydb-platform/ydb/pull/48410).

### 6.160. Mixed nested ``toc_i`` shell must not break EN YAML (#48409 / #44466, 2026-07-30)

**Problem:** [#48409](https://github.com/ydb-platform/ydb/pull/48409) QA 🟢 but
docs build failed:

```text
Unable to resolve en/.../alter_table/toc_i.yaml
end of the stream or a document separator is expected (4:1)
- name: COLUMN
```

Source [#44466](https://github.com/ydb-platform/ydb/pull/44466) promoted RU
``COLUMN`` from inline ``- { href: columns.md }`` to a nested section with
``FAMILY`` / ``NOT NULL`` children. ``planned_toc_extras_for_pair`` put
``columns.md`` in toc translate scope because that page was also translated.
Flat merge then emitted the truncated RU block:

```yaml
- name: COLUMN
  href: columns.md
  items:
```

and concatenated EN-prefixed siblings (`` - { name: FAMILY …}``) after it →
invalid YAML. (Broken rebuild workflow §6.159 masked/cancelled the real build
as well.)

**Decision:**

1. When a scoped RU block is a nested ``items:`` **shell** (opens ``items:``
   but has no child list entries in the same block), keep the EN leaf block
   (name-translated) or emit a plain ``_leaf_block`` — never the empty shell.
2. ``validate_toc_merge`` treats unparseable EN toc YAML as blocking
   ``invalid_yaml``.

**Tests:** ``test_pr_48409_mixed_nested_column_shell_stays_valid_yaml``,
``test_validate_toc_merge_flags_invalid_yaml``.

### 6.161. ``rebuild_docs`` cancels PR ``build-docs`` check (#48409, 2026-07-31)

**Problem:** [#48409](https://github.com/ydb-platform/ydb/pull/48409) content built
successfully via ``workflow_dispatch``, QA 🟢, but the PR Checks tab still
showed ``build-docs`` failed/cancelled.

Root cause (ydb CI, not translation content):

1. ``pull_request`` starts ``docs_build.yaml`` → check run on **PR head SHA**.
2. ``trigger-translation-ci`` adds ``rebuild_docs`` → dispatch from **main**.
3. Shared concurrency ``docs-build-<PR>`` + ``cancel-in-progress`` cancels (1).
4. Successful dispatch check attaches to **main SHA**, not the PR head → PR
   keeps the cancelled check.

**Decision (ydb [#48439](https://github.com/ydb-platform/ydb/pull/48439)):**

1. Concurrency group includes ``event_name`` so dispatch does not cancel the
   ``pull_request`` build.
2. Auto-label after ``doc_translate``: only ``ok-to-test`` (not ``rebuild_docs``).
   Manual ``rebuild_docs`` remains for explicit rebuilds/preview.

### 6.162. Wire ``YDBDOC_ALLOWED_ACTORS`` into live ``doc_verify`` (2026-07-31)

**Problem:** Phase K ACL (§6.134) already gates ``doc_translate`` /
``doc_verify`` / ``doc_continue`` in Python when ``YDBDOC_ALLOWED_ACTORS`` and
``GITHUB_ACTOR`` are set. The ydb example
``examples/ydb-github-doc-verify-on-label.yml`` passed those env vars, but the
**live** ``ydb-platform/ydb`` workflow ``.github/workflows/ydbdoc-verify.yml``
did not — so anyone who could add the ``doc_verify`` label burned LLM budget
while ``doc_translate`` stayed allowlisted (``sintjuri`` only).

**Decision:**

1. Pass the same ops env on verify as on translate:
   ``GITHUB_ACTOR``, ``YDBDOC_ALLOWED_ACTORS``, ``YDBDOC_DAILY_BUDGET_RUB``,
   ``YDBDOC_TRANSCRIPT_BACKEND``, ``YDB_SA_KEY``.
2. Keep a single repo variable ``YDBDOC_ALLOWED_ACTORS`` for all three labels
   (no separate verify allowlist).
3. Deploy via ydb [#48518](https://github.com/ydb-platform/ydb/pull/48518)
   (``ydbdoc-verify.yml``); example already matched.

### 6.163. Differential seed requires kind-aligned EN + block unrestored placeholders (#48595 / #46798, 2026-08-03)

**Problem:** Translation PR [#48595](https://github.com/ydb-platform/ydb/pull/48595)
(from RU-only [#46798](https://github.com/ydb-platform/ydb/pull/46798), after
earlier RU rewrite [#43693](https://github.com/ydb-platform/ydb/pull/43693))
shipped a broken EN ``client_certificate_authorization.md``:

1. **Stale EN, same segment count:** main EN still described *node*
   authentication (5 intro paragraphs, 7 table rows); current RU describes
   *device* auth (3 intro paragraphs + ``## Синтаксис``, 8 table rows). Counts
   matched (26↔26) so ``align_translations_from_target`` accepted a positional
   zip. Differential seeded EN paragraph text onto the RU ``## Синтаксис``
   heading → ``## The "Subject" field…``, table headers/cells scrambled,
   meaning drift, missing ``authentication.md`` / ``index.md`` /
   ``node-authorization.md`` links. Critic suggestions were correct but
   rejected by pipeline protection (placeholder / regression guards).
2. **Unrestored protect markers** in the final EN (``⟦V1⟧``, ``⟦C1⟧``,
   percent-encoded ``%E2%9F%A6U1%E2%9F%A7``) — §6.114 covered the image-bang
   case only; prose/link leftovers were not a blocking heuristic.

**Decision:**

1. ``align_translations_from_target`` / round-trip gate require matching
   segment **kinds** at each index, not only equal length. Kind drift →
   ``TranslationValidationError`` → empty differential seed → **full**
   retranslate (§6.132 fallback).
2. New blocking heuristic ``unrestored_placeholder:`` on leftover
   ``⟦[CLIHVTUS]n⟧`` and URL-encoded forms in final EN.

**Manual follow-up on #48595:** rewrite EN ``client_certificate_authorization.md``
to match current RU (device-auth framing); other six files in that PR were 🟢.

**Tests:** ``test_align_rejects_kind_mismatch_same_count``,
``test_prepare_seed_falls_back_full_on_kind_mismatch``,
``test_unrestored_placeholder_blocks``.


### 6.164. Residual Cyrillic + protect markers must block merge (#48595, 2026-08-03)

**Problem:** On translation PR [#48595](https://github.com/ydb-platform/ydb/pull/48595)
verify marked ``glossary.md`` 🟢 while EN still contained literal ``⟦V2⟧``, and
``client_certificate_authorization.md`` kept YAML angle-bracket Russian
(``<SID по умолчанию>``, ``<массив SID>``, …) plus leftover ``⟦C*⟧`` /
percent-encoded markers. Critic saw meaning drift on the cert page (🔴) but
auto-fixes were rejected by pipeline protection; glossary/placeholders and
fence Cyrillic escaped the merge gate:

1. **Protect markers** — ``unrestored_placeholder`` (§6.163) did not exist yet
   at that verify run; glossary with ``⟦V2⟧`` was green.
2. **YAML / code-fence Cyrillic** — ``check_cyrillic_in_en`` strips *all*
   fences, and ``cyrillic_in_fence`` / ``cyrillic_in_text_fence`` only cover
   comment lines / `` ```text ``. Cyrillic inside yaml example placeholders
   was invisible. Those fence helpers were also classified as **warnings**,
   so even comment leftovers would not force 🔴.

**Decision:**

1. Keep / broaden ``unrestored_placeholder`` (any ``⟦…⟧`` + encoded form) as
   **blocking**.
2. New ``check_cyrillic_in_en_all_fences`` → ``cyrillic_in_code_fence:`` for
   **any** fenced language (yaml/yql/go/text/…).
3. Promote residual Cyrillic helpers and ``*_translate_skipped`` to
   **blocking** (never soft-warn merge).
4. Critic / verify prompts: residual Cyrillic or broken placeholders →
   severity ``blocked`` (hard), not soft warning.

Deterministic heuristics are the hard gate; critic prompts reinforce the same
bar.

**Tests:** ``test_unrestored_placeholder_blocks_glossary_v2``,
``test_cyrillic_in_yaml_fence_blocks``, updated fence-comment classification
tests.


### 6.165. Toc extras must use translated docs only; bilingual keep EN labels (#48411 / #48589, 2026-08-03)

**Problem:** Source PR [#48411](https://github.com/ydb-platform/ydb/pull/48411)
(26 files: DevOps → Cluster Administration) produced translation PR
[#48589](https://github.com/ydb-platform/ydb/pull/48589) with only ~3–5 files.
Most pairs were correctly skipped (§6.76 bilingual). The real bug: EN
``toc_i.yaml`` **regressed** good main labels (``Quick start`` →
``Getting started``, ``Cluster Administration`` → ``Cluster administration``)
and reshuffled Public materials vs Downloads.

**Root cause:** ``planned_toc_extras_for_pair`` fed **full**
``plan.doc_ru_paths`` into toc name-translate scope, including bilingual-
skipped pages (``quickstart.md``, ``devops/index.md``). Those hrefs entered
LLM menu-label rewrite. Separately, when ``pair.en_changed``, RU renames still
re-LLMed labels already present on EN main.

**Decision:**

1. ``planned_toc_extras_for_pair(..., active_doc_ru_paths=)`` — when set,
   only paths **actually translated** (post §6.76) count as href extras.
   Workflow passes ``frozenset(p.ru_path for p in pairs)`` on merge and verify.
2. ``_resolve_toc_merge_scope``: if ``pair.en_changed``, drop href/include
   already on EN main from name scope; keep extras for pages not yet on EN.

**Tests:** ``tests/unit/test_toc_bilingual_extras.py``.


### 6.166. Re-``doc_translate`` force-with-lease onto ``ydbdoc-review/pr-*`` (#46798, 2026-08-03)

**Problem:** Re-running ``doc_translate`` on merged source
[#46798](https://github.com/ydb-platform/ydb/pull/46798) finished translate +
local commit, then crashed at push:

```
! [rejected] HEAD -> ydbdoc-review/pr-46798 (non-fast-forward)
```

[#48595](https://github.com/ydb-platform/ydb/pull/48595) already had the
branch tip (translate + verify critic commit). ``prepare_translation_branch_on_base``
rebuilds from upstream ``main``, so the new tip is not a FF of the remote.
``doc_verify`` fixup path deletes the stale remote ref (§6.52); ``doc_translate``
did not.

**Decision:** ``push_branch(..., force=True)`` for ``run_doc_translate``
uses plain ``--force`` (not ``--force-with-lease``). The action checkout
never fetches the remote ``ydbdoc-review/pr-*`` tip, so lease checks fail
with ``(stale info)`` even when a rewrite is intended. Keeps / recreates
the translation PR head. Verify fixup delete path unchanged (§6.52).

**Ops note:** deleting the remote ``ydbdoc-review/pr-*`` branch before
re-label also unblocks plain push (closes the old translation PR).

**Tests:** ``test_push_branch_force``.


### 6.167. Never translate ``public-materials/*`` (#48756 / #48411, 2026-08-03)

**Problem:** Translation PR [#48756](https://github.com/ydb-platform/ydb/pull/48756)
mirrored a RU ``toc_i.yaml`` reshuffle of the **Public materials** sidebar
entry (``public-materials/toc_p.yaml``). Ops decision: that tree is
out of auto-translate scope.

**Decision:**

1. ``paths.translate_skip_globs`` default
   ``**/public-materials/**`` + ``public-materials/**``.
2. ``filter_translate_changes`` / scope-plan path filter drop matching docs
   and nav under that tree before translate/verify.
3. After toc merge, ``preserve_en_order_for_skipped_toc_entries`` keeps
   matching href/include entries at their **EN-main** slots so a parent
   ``toc_i`` RU reorder of Public materials is a nav no-op on EN.

Guide page ``contributor/.../guide-to-public-material.md`` is also skipped
(same topic; path is outside the ``public-materials/`` tree — #48760).

**Tests:** ``tests/unit/test_translate_skip_paths.py``.


### 6.168. Partial differential seed when EN↔RU structure diverges (#48762 / #46798, 2026-08-03)

**Problem:** Re-translate of [#46798](https://github.com/ydb-platform/ydb/pull/46798)
→ [#48762](https://github.com/ydb-platform/ydb/pull/48762) went 🔴:

1. ``glossary.md`` — leftover ``⟦V2⟧`` and meaning loss on *client certificate*
   (main already had a good EN sentence ending in ``{{ ydb-short-name }}``).
2. ``client_certificate_authorization.md`` — Cyrillic YAML angle placeholders
   (``<SID по умолчанию>``, ``<массив SID>``, …).

**Root cause:** when ``align_translations_from_target`` failed (RU base 27 vs
EN 26 segments), differential used an **empty** seed plan → full-page
retranslate overwrote good EN. Angle map lacked cert-page phrases; fences were
copied from RU via ``enforce_source_fenced_blocks``.

**Decision:**

1. ``partial_align_translations_from_target`` — seed kind-matched **prefix +
   suffix**; only the structural wedge is LLM-translated (§6.168).
2. Expand ``_ANGLE_PLACEHOLDER_EN`` for cert YAML placeholders.

**Tests:** ``tests/unit/test_differential_partial_seed.py``.

### 6.169. LCS partial seed + percent-encoded protect reinsert (#48764 / #46798, 2026-08-03)

**Problem:** After §6.168, re-translate of [#46798](https://github.com/ydb-platform/ydb/pull/46798)
→ [#48764](https://github.com/ydb-platform/ydb/pull/48764) still 🔴. Logs showed
``authentication.md`` seeded only **9/141** segments; mass unrestored ``⟦…⟧`` and
``%E2%9F%A6…%E2%9F%A7`` across auth/security/glossary.

**Root cause:**

1. Prefix+suffix partial align collapses when an **early** kind wedge appears —
   the whole middle+suffix is treated as the gap → near-full retranslate.
2. When LLM leaves protect markers inside markdown link/image hrefs, render can
   percent-encode ``⟦U1⟧``; reinsert only matched literal markers → leftovers.

**Decision:**

1. ``partial_align_translations_from_target`` uses **LCS over segment kinds**,
   then kind-normalizes each matched pair (keeps large stable islands).
2. ``_split_text_by_placeholders`` also matches ``%E2%9F%A6Xn%E2%9F%A7`` and
   ``unquote``s to the mapping key.
3. ``decode_percent_encoded_protect_markers`` in EN postprocess so remaining
   encoded markers become literal ``⟦…⟧`` for heuristics.

**Tests:** ``test_differential_partial_seed.py`` (LCS wedge),
``test_reinsert_percent_placeholders.py``.

### 6.170. Partial seed must match placeholder multiset (#48773 / #46798, 2026-08-03)

**Problem:** After §6.169, [#48773](https://github.com/ydb-platform/ydb/pull/48773)
was still 🔴: ``authentication.md`` had **79** leftover ``⟦…⟧``, plus markers on
glossary / tls / security/index / client_certificate_authorization.

**Root cause:** RU ``authentication.md`` has **141** segments vs EN **90**. Kind-only
LCS paired unrelated paragraphs (e.g. RU «Поддерживаются следующие виды…» ← EN
«An authentication client… ⟦V1⟧»). Seeded EN protect markers were reinserted
against a RU segment whose ``placeholders`` list did not contain those ids →
literal ``⟦V1⟧`` / ``⟦C…⟧`` in the published file. Reproduced locally: 66/89
LCS seeds failed ``placeholders_match``.

**Decision:**

1. LCS key = ``(kind, placeholder-letter signature)`` not kind alone.
2. After normalize, **drop** pairs where ``placeholders_match(src, en)`` is false.
3. Same gate when applying ``base_en`` onto PR segments in
   ``DifferentialTranslationAnalyzer.plan_translation``.

**Tests:** ``test_partial_align_rejects_placeholder_mismatched_lcs_pairs``.

### 6.171. Refuse weak / high-drift partial seeds (#48780 / #46798, 2026-08-03)

**Problem:** After §6.170, [#48780](https://github.com/ydb-platform/ydb/pull/48780)
was 🟢 on heuristics (no ``⟦…⟧``) but **content was wrong**: LDAP numbered steps
contained IAM «Refresh Token» / Anonymous bullets; password brute-force and
manual lockout section bodies were swapped.

**Root cause:** ``authentication.md`` RU/EN segment counts 141 vs 90 (drift
~36%). LCS still seeded **34** empty/``⟦V⟧``-only paragraphs that shared the
same signature — placeholder multiset matched, meaning did not.

**Decision:**

1. If segment-count drift ``> 0.25``, return **empty** partial seed → full LLM
   translate for the file.
2. Otherwise seed only **trustworthy** pairs: non-variable placeholder
   fingerprint (C/U/…), or short heading with length parity. Plain / V-only
   paragraphs are never LCS-seeded.
3. Same trust gate when applying ``base_en`` onto PR segments.

**Tests:** ``test_partial_align_refuses_high_structure_drift``,
``test_partial_align_rejects_weak_empty_paragraph_pairs``.

### 6.172. Placeholder-only segments stay marker-only (#48785 / #46798, 2026-08-03)

**Problem:** [#48785](https://github.com/ydb-platform/ydb/pull/48785) was mostly
clean, but ``client_certificate_authorization.md`` table key for
``default_group`` became a full English sentence that still contained the
backticked name — QA stayed 🟢.

**Root cause:** key cells are a single ``⟦C1⟧``. The LLM “translated” them into
prose wrapping the same marker. ``placeholders_match`` still passed.

**Decision:**

1. ``is_placeholder_only_text`` — segment is only markers + whitespace.
2. ``validate_segment_translation`` rejects prose elaboration of such segments.
3. ``translate_segments`` copies placeholder-only text as-is (no LLM).
4. ``partial_seed_is_trustworthy`` refuses seeding prose onto placeholder-only
   sources.

**Tests:** ``tests/unit/test_placeholder_only_segments.py``.

### 6.173. Block unrestored ``yfmvar-N-yfmvarend`` in EN (#48812, 2026-08-04)

**Problem:** [#48812](https://github.com/ydb-platform/ydb/pull/48812) cleaned EN
``indexes.md`` that still had Cyrillic and broken hrefs like
``](yfmvar-0-yfmvarend#sync)`` instead of ``{{ concept_secondary_index }}``.
Damage came from an earlier auto-translate ([#47995](https://github.com/ydb-platform/ydb/pull/47995)).

**Root cause:** ``link_with_variable`` replaces ``{{ var }}`` in hrefs with
``yfmvar-N-yfmvarend`` during parse. If restore fails, the stand-in leaks into
published EN. ``unrestored_placeholder`` (§6.163) only matches ``⟦…⟧`` /
percent-encoded protect markers — not ``yfmvar-*``.

**Decision:**

1. New blocking heuristic ``unrestored_yfmvar:`` on leftover
   ``yfmvar-\d+-yfmvarend`` in EN.
2. Russian QA copy in ``heuristic_messages`` (плейсхолдеры yfmvar).

**Tests:** ``test_unrestored_yfmvar_blocks``.

### 6.174. RU↔EN link 1:1 parity; no EN-only fragment remaps (#48792, 2026-08-04)

**Problem:** Merging [#48792](https://github.com/ydb-platform/ydb/pull/48792)
rewrote EN ``authentication.md`` ``{#ldap-auth-provider}`` → ``{#ldap}`` (matching
RU). ``create-resource-pool-classifier.md`` still linked to
``#ldap-auth-provider`` → YFM010 on ``main``. QA was 🟢: scope was only the
translated files; §6.142 *outbound* repair had taught the pipeline to **create**
EN-only fragments.

**Decision (simplify):**

1. **Policy:** internal hrefs and explicit ``{#id}`` on a translated page must
   match the RU twin one-to-one (same path#fragment, no extras).
2. Blocking heuristics: ``href_parity:``, ``anchor_parity:``,
   ``inbound_fragment:`` (other EN pages → missing frag on the new EN page).
3. ``repair_en_fragments`` only retargets **paths** when the fragment id is
   shared (``index.md#sessions`` → ``execution_process.md#sessions``). It no
   longer remaps ``#ldap`` → ``#ldap-auth-provider`` or prefix-extends frags.

**Tests:** ``tests/unit/test_href_parity.py``,
``test_pr_48047_ldap_does_not_remap_to_en_only_fragment``.

### 6.175. Post «перевод не требуется» on bilingual-only noop (#48751, 2026-08-05)

**Problem:** [#48751](https://github.com/ydb-platform/ydb/pull/48751) changed RU+EN
glossary in one PR. ``doc_translate`` correctly skipped auto-translate (§6.76)
via ``skip_en_paths``, logged ``No doc or navigation pairs``, exited early —
and **never posted** the source-PR comment that ``build_source_pr_comment``
already knew how to write for bilingual skips.

**Root cause:** bilingual pairs are filtered out of ``pairs`` before analyze, so
``PRTranslationResult`` was empty and the early return skipped
``_safe_post_issue_comment``.

**Decision:** on empty ``pairs``/``nav_pairs``, synthesize skipped
``PairRunResult``s from ``bilingual_en_mirrors`` and post the §6.76 source
summary when any markdown bilingual skips exist.

**Tests:** ``test_run_doc_translate_bilingual_skip_posts_source_comment``.

### 6.176. Partial seed headings by ``{#anchor}``; block mangled inline code (#49040 / #48968, 2026-08-05)

**Problem:** Translation PR [#49040](https://github.com/ydb-platform/ydb/pull/49040)
(from [#48968](https://github.com/ydb-platform/ydb/pull/48968)) had QA 🟢 but bad EN
``suggest-change.md``:

1. ``### Precommit checks {#create_pr_desc}`` — body is Changelog / PR description
   (RU ``Заполните описание… {#create_pr_desc}``); next section correctly
   ``Preliminary checks {#precommit_checks}``.
2. Mangled SSH path: ``( extension)`` and
   ``**/home/user/.ssh/id_ed25519`.pub`**`` instead of ``(расширение `.pub`)`` /
   ``**/…/id_ed25519.pub**``.

**Root cause:** partial LCS seed (§6.168–§6.171) keyed headings by kind only
(+ length ratio). When RU added ``{#create_pr_desc}`` and kept
``{#precommit_checks}``, LCS paired the new RU heading with stale EN
«Precommit checks». Render then attached the RU anchor → wrong title, right id.
The ``.pub`` damage was model/critic noise that heuristics never blocked.

**Decision:**

1. Store explicit ``heading_anchor`` on ``Segment``; include it in the partial
   LCS key; refuse heading seeds when anchors differ (§6.176 gate).
2. Blocking heuristic ``broken_inline_code:`` for bold+mid-path backtick nesting
   and empty ``( extension)`` parentheticals.

**Tests:** ``test_partial_align_rejects_heading_with_mismatched_anchor``,
``test_broken_inline_code_markup_blocks``.

### 6.177. ``broken_inline_code`` only for path+extension split (#49059, 2026-08-06)

**Problem:** Re-translate of [#48968](https://github.com/ydb-platform/ydb/pull/48968)
produced good EN (correct ``{#create_pr_desc}`` title, intact ``.pub`` path) but
QA 🔴 on [#49059](https://github.com/ydb-platform/ydb/pull/49059) because
``broken_inline_code:`` matched legitimate ``**Box `workflow`**``.

**Decision:** narrow the bold+backtick regex to path fragments that end with a
backticked ``.ext`` only (``…/id_ed25519`.pub`**``). Keep empty
``( extension)`` paren check. Valid ``**… `code` …**`` is allowed.

**Tests:** ``test_broken_inline_code_allows_bold_wrapping_code``.

### 6.178. Toc merge: mark href+include together; block duplicates (#49147, 2026-08-06)

**Problem:** Translation PR [#49147](https://github.com/ydb-platform/ydb/pull/49147)
shipped a green QA report while EN ``reference/toc_p.yaml`` had **duplicate
Embedded UI** entries. Soft ``toc_en_only_legacy`` did not block.

**Root cause:** RU lists Embedded UI as **include-only**; EN has **href +
include**. Merge matched via ``include.path`` and marked only the include as
seen, then leftover re-appended the same EN block (href not in RU).

**Decision:** when applying an EN block, mark **both** ``href`` and
``include.path``; leftovers skip if either id was seen; ``duplicate_toc_entry``
is **blocking**.

**Tests:** ``test_merge_ru_include_only_matches_en_href_plus_include_without_duplicate``,
``test_validate_toc_merge_flags_duplicate_toc_entry``.

### 6.179. Restore EN md / autotitle hrefs after translate (#49451, 2026-08-10)

**Problem:** Translation PR [#49451](https://github.com/ydb-platform/ydb/pull/49451)
(from [#45219](https://github.com/ydb-platform/ydb/pull/45219)) was 🔴 on
``href_parity`` / ``md_link_parity``:

1. Glossary dropped three ``architecture/metadata-services.md`` links (plain
   ``see the section …``).
2. ``local_indexes.md`` pointed at ``secondary_index.md#example`` instead of
   ``min_max_index.md#example``.
3. Critic removed ``[{#T}](static-group-self-heal.md)`` while adding
   ``state-storage-reconfiguration.md``.

**Decision:** after RU→EN translate/verify, run:

1. ``restore_md_link_hrefs`` — positional href force when link counts match;
   reinject ``see the section [Title](href).`` when RU hrefs are missing.
2. ``insert_missing_autotitle_list_items`` — splice missing ``[{#T}](…)``
   bullets after the previous shared neighbor.

**Tests:** ``test_restore_md_link_hrefs_*``,
``test_insert_missing_autotitle_list_items_splices_neighbor``.

### 6.180. Skip unreachable EN targets on href restore (#49451, 2026-08-10)

**Problem:** After §6.179, ``insert_missing_autotitle_list_items`` re-inserted
RU-only ``state-storage-reconfiguration.md`` (no EN page / toc), while critic
or strip dropped ``static-group-self-heal.md``. Verify then fought the manual
fix and stayed 🔴 on ``href_parity``. Heuristics also ran *before* restore, so
the report did not match committed text.

**Decision:**

1. ``insert_missing_autotitle_list_items`` — skip RU hrefs whose resolved EN
   path is outside ``en_toc_reachable``.
2. ``href_parity`` (via heuristics) — ignore the same unreachable basenames
   (same as ``md_link_parity``).
3. ``run_pair_plan`` — after restore changes text, re-run classified heuristics
   and ``compose_file_verdict`` so the PR report matches committed EN.

**Tests:** ``test_insert_missing_skips_unreachable_en_targets``.

### 6.181. Inbound fragment path resolve + ambient skip (#49451, 2026-08-10)

**Problem:** Re-scoring heuristics after restore (§6.180) surfaced false
``inbound_fragment`` on every ``…/index.md`` (basename-only match for
``index.md#frag`` from unrelated sections) and ambient glossary typos
(``#tablets`` vs RU ``{#tablet}``). Critic + ``force_exact`` also reattached
RU-only ``state-storage-reconfiguration.md``.

**Decision:**

1. Resolve inbound hrefs from the linking file (no basename-only match).
2. Flag inbound only when RU declares the id or the EN baseline dropped it
   (§6.174 rename hole); ignore never-declared ambient typos.
3. ``restore_autotitle_hrefs(force_exact)`` skips unreachable RU hrefs; strip
   again after restore so critic cannot leave RU-only links.

**Tests:** ``test_inbound_ignores_same_basename_other_dirs``,
``test_inbound_ignores_frag_absent_from_ru_and_baseline``,
``test_restore_autotitle_force_exact_skips_unreachable``.

### 6.182. Allow reachable EN-extra hrefs vs source-PR RU (#49451, 2026-08-10)

**Problem:** Source PR [#45219](https://github.com/ydb-platform/ydb/pull/45219)
merge-commit RU lacked ``static-group-self-heal.md``; ``main`` and the
translation EN index both have it. ``doc_verify`` preferred merge RU (segment
count match) → 🔴 ``href_parity`` «extra in EN: self-heal» while reconfig was
already ignored as unreachable.

**Decision:** ``check_href_parity`` drops EN-extra hrefs whose targets resolve
inside ``en_toc_reachable`` (EN documents a real toc page).

**Tests:** ``test_href_parity_allows_reachable_en_extras``.

### 6.183. Progress logs + LLM heartbeat for long CI runs (#45667, 2026-08-10)

**Problem:** ``doc_translate`` for [#45667](https://github.com/ydb-platform/ydb/pull/45667)
spent 40+ minutes with no visible progress via ``gh`` (logs only after job end);
glossary timed out without knowing which step was stuck.

**Decision:** log ``pair i/n start/done``, each file harness step, and LLM
call start/done with a 30s heartbeat while the HTTP request blocks.

**Tests:** ``test_llm_call_heartbeat_emits_waiting``.

### 6.184. Low-magnitude EN patch for tiny RU glossary edits (#45667, 2026-08-11)

**Problem:** Source PR [#45667](https://github.com/ydb-platform/ydb/pull/45667)
adds ``metadata-services.md`` plus three short glossary cross-links. Differential
still left **100+** unchanged glossary segments pending LLM because RU/EN
structure drifts and §6.171 refuses weak LCS seeds. One Eliza timeout on
``glossary.md`` opened a §6.80 completeness gap and aborted the whole
translation PR. First ship of the patch path still **reconstructed** EN when
``slim == pending`` or pending was empty after relaxed seed — producing
garbled glossary on [#49578](https://github.com/ydb-platform/ydb/pull/49578).

**Decision:**

1. After a trustworthy partial seed covers ≥40% of base segments (real ratio,
   not ``int(0.4*n)``), allow a **relaxed** equal-opcode LCS map
   (``require_trustworthy=False``) only to reuse EN for *unchanged* RU
   segments. Weak strict maps still fall back to full (§6.163 / #48595).
2. When RU change magnitude &lt; 5%, **always** keep existing EN: LLM only
   added/modified segments and **splice/replace** under the preceding
   ``{#anchor}`` (``patch_en_with_added_translations``). Never reconstruct
   the page — including when pending is empty or already equals the change set.
3. On translate LLM failure with existing EN, **keep** the current EN so
   completeness does not kill sibling files (e.g. new ``metadata-services.md``).

**Tests:** ``test_differential_low_magnitude_patch.py``,
``test_prepare_seed_falls_back_full_on_kind_mismatch``,
``test_run_pair_plan_keeps_existing_en_on_translate_llm_failure``.

### 6.185. Cap verify realign on large files (#49578, 2026-08-11)

**Problem:** Inline / label ``doc_verify`` on [#49578](https://github.com/ydb-platform/ydb/pull/49578)
hung 30+ minutes with no QA report. After translate, verify hit
``glossary.md`` segment mismatch (449 vs 448) and §6.147 **retranslated all
RU segments** via Eliza; individual calls timed out at ~362s with retries.

**Decision:** skip full verify realign when ``len(segments) > 80``. Keep
existing EN, leave ``segment_alignment_error`` so the file is 🔴 and the job
can finish and post the report. Small pages still realign as in §6.147.

**Tests:** ``test_verify_realign_skips_full_retranslate_for_large_files``.


### 6.186. Skip glossary structural alignment on verify (#49578, 2026-08-11)

**Problem:** ``glossary.md`` hub pages have long-standing RU/EN segment drift
(heading vs paragraph at the same index). ``gate_round_trip`` always failed →
🔴 alignment blocked merge even when content was fine. §6.185 skipped full
realign but still left the error as blocker.

**Decision:** on ``doc_verify``, for ``concepts/glossary.md`` clear
``segment_alignment_error`` after logging; run critic/heuristics on existing
EN. Also extend Cyrillic auto-translate to `` ```mermaid `` fences (labels
were copied from RU via ``enforce_source_fenced_blocks`` but never translated).

**Tests:** ``test_glossary_verify_clears_alignment_error``,
``test_collect_cyrillic_mermaid_fence_lines`` (in ``test_fence_comments``).

### 6.187. Skip glossary finalize on verify (#49578, 2026-08-11)

**Problem:** ``finalize_en`` on ``glossary.md`` during ``doc_verify`` ran
``enforce_source_fenced_blocks`` + prose/Cyrillic LLM over 400+ segments (~35
min) and rewrote hundreds of EN lines; push then failed locally (403).

**Decision:** on ``doc_verify`` for ``concepts/glossary.md``, skip
``finalize_en`` — keep existing EN; heuristics + critic suffice.

**Tests:** covered by ``test_glossary_verify_clears_alignment_error`` profile
(extend if needed).

### 6.188. Skip glossary critic on verify (#49578, 2026-08-11)

**Problem:** even with alignment/finalize skipped, ``critic_loop`` on
``glossary.md`` ran ~35 min over 400+ segments and hybridized EN (RU paragraphs
mixed into EN) before auto-commit.

**Decision:** on ``doc_verify`` for ``concepts/glossary.md``, skip
``critic_loop`` — heuristics-only QA on existing EN.

**Tests:** ``test_glossary_verify_clears_alignment_error`` (harness profile).

### 6.189. Never write glossary EN from verify (#49578, 2026-08-11)

**Problem:** ``doc_verify`` auto-commit ``0e760c73`` pushed 747-line glossary
diff (170+ Cyrillic lines) after critic/finalize hybridized ``target_text``;
§6.188 stopped new hybridization but disk write could still commit stale
``PairRunResult.target_text``.

**Decision:** in ``_apply_results_to_disk``, skip writing ``concepts/glossary.md``
when ``plan.action == critic_only`` (verify path). Glossary fixes belong in
``doc_translate``, not verify auto-push.

**Tests:** ``test_run_doc_verify_skips_glossary_disk_write``.

### 6.190. Mermaid quoted labels: ignore hyphen/word-count drift (#49578, 2026-08-11)

**Problem:** After translating mermaid node labels, ``fence_body_copy`` still
warned: RU ``["Дата-центр …"]`` / ``["Хеш-функция\\n…"]`` normalize to
``*-*`` while EN ``["Data center …"]`` / ``["Hash function\\n…"]`` normalize
to ``* *`` (different token count). Yellow-blocked [#49578](https://github.com/ydb-platform/ydb/pull/49578)
despite intentional label translation (§6.186).

**Decision:** in ``_mermaid_structure_line``, collapse whole ``["…"]`` /
``['…']`` quoted labels to ``[*]`` before word tokenization.

**Tests:** ``test_fence_content_allows_mermaid_quoted_hyphen_vs_space_labels``.


### 6.191. Structural EN repair + partial verify realign (#49957, 2026-08-18)

**Problem:** Translation PR [#49957](https://github.com/ydb-platform/ydb/pull/49957)
(source [#49800](https://github.com/ydb-platform/ydb/pull/49800)) stayed 🔴 until a
tech writer fixed three files manually (commits ``4f3c077``, ``798979a``):

1. ``example-dotnet.md`` — RU ``{#csharp-app}`` dropped from EN H1.
2. ``permissions_list.md`` — missing table section row
   ``**Rights based on other rights**`` (81 RU vs 80 EN segments).
3. ``aggregation.md`` — ~27 missing ``### Signature`` + `` ```yql`` blocks; verify
   realign skipped (180 segments > §6.185 cap).

**Root cause:**

- Low-magnitude **differential patch** (§6.184) splices changed paragraphs into
  stale EN but does not insert new structural blocks (signature headings + YQL
  fences, table divider rows).
- ``anchor_parity`` detected missing ``{#csharp-app}`` but did not repair.
- §6.185 skipped **full** verify realign on large files; no gap-only fallback.

**Decision:**

1. ``repair_en_structure_from_ru`` after translate / restore / verify load:
   ``restore_explicit_heading_anchors`` (AST ``heading.anchor`` → EN line),
   ``sync_missing_signature_sections`` (copy language-neutral `` ```yql`` blocks).
2. On verify alignment mismatch: try **partial realign** first — LCS-seed
   existing EN, LLM-translate only unmatched RU segments (cap 80 pending), render
   from RU structure; then fall back to §6.147 full realign or §6.185 skip.

**Tests:** ``tests/unit/test_structural_repair.py``,
``tests/unit/test_verify_partial_realign.py``,
``test_run_pair_plan_restores_missing_heading_anchor_after_translate``.

### 6.192. Keep RU ``{#id}``; restore glued code markers; toc EN-absent siblings (#37673, 2026-08-21)

**Problem:** ``doc_translate`` on merged [#37673](https://github.com/ydb-platform/ydb/pull/37673)
produced [#50684](https://github.com/ydb-platform/ydb/pull/50684) 🔴:

1. ``anchor_parity`` — EN ``{#fields-Response}`` vs RU ``{#fields-Описание}``
   (``english_yfm_anchor`` on render, conflicting with §6.174).
2. Leftover ``⟦C3⟧`` inside inline code
   (``[`⟦C3⟧_subscriber::fmt`](…)``) — reinsert skipped ``InlineCode.content``.
3. Toc ``scope_not_applied`` for ``debug.md`` — diff had ``debug-logs.md`` etc.,
   scope planner did not queue the EN-absent overview sibling, so nav merge
   emitted children without ``debug.md``.

**Decision:**

1. EN heading render keeps explicit ``{#id}`` as on the RU twin (no
   ``english_yfm_anchor`` rewrite). ``restore_explicit_heading_anchors`` also
   **overwrites** mismatched EN ids.
2. ``_substitute_placeholders`` expands markers inside ``InlineCode``, including
   glued tails / duplicated suffixes after a whole-atom marker.
3. When a sidebar is in ``nav_ru`` next to a diff page, queue every EN-absent
   ``href`` from that RU toc (``debug.md`` next to ``debug-logs.md``).

**Tests:** ``test_cyrillic_anchor_parsed_and_rendered_in_english``,
``test_restore_explicit_heading_anchors_overwrites_mismatched_en_id``,
``test_substitute_expands_glued_marker_inside_inline_code``.

### 6.193. Refuse low-magnitude EN patch on fence/tab drift (#37673 / #50684, 2026-08-21)

**Problem:** Re-translate of [#37673](https://github.com/ydb-platform/ydb/pull/37673)
→ [#50684](https://github.com/ydb-platform/ydb/pull/50684) left SDK recipes
truncated: ``distributed-lock.md`` EN kept 2 language panes while RU had 6;
``health-check-api.md`` nested broken tabs; ``topic.md`` / ``vector-search.md``
alignment 🔴. ``verify_realign_partial:`` was also treated as blocking.

**Root cause:** §6.184 low-magnitude patch **splices into existing EN** and never
reconstructs the RU ``{% list tabs %}`` tree. When main EN and merge-commit RU
diverge (or a prior bad EN is short), missing panes stay missing.
``list_tab_parity`` only counts containers, not panes.

**Decision:**

1. Before low-magnitude patch: if ``fence_parity``, ``list_tab_parity``, or
   YfmTab pane counts differ RU↔existing EN → **skip patch**, full reconstruct
   from RU AST.
2. ``verify_realign_partial:`` → info (same family as ``verify_realign:``).
3. Whitelist ``Python (alternative)`` / ``Python (альтернативный)`` as lang
   panes so they do not emit ``tab_title`` segments (§6.79-style).

**Tests:** ``tests/unit/test_low_magnitude_structure_gate.py``.

### 6.194. Parse ``group=…`` tabs; toc-queue EN orphans (#37673, 2026-08-21)

**Problem:** Re-analysis of [#50684](https://github.com/ydb-platform/ydb/pull/50684)
🔴 after §6.192–§6.193 showed two remaining root causes:

1. ``{% list tabs group=lang %}`` (and ``group=manual-systemd``, …) did **not**
   match the YFM tabs plugin (regex only allowed a bare ``\w+`` token). Health-check
   / ``topic.md`` tabs became ordinary ``BulletList`` + literal ``{% list tabs %}``
   paragraphs → wrong segmentation, pane gate always ``0``, mangled EN after
   low-magnitude splice.
2. ``debug.md`` overview: EN **file** still exists on main (orphan) while EN
   **toc** dropped the Diagnostics section. §6.192 ``_add_doc_if_en_absent``
   skipped it → children (``debug-logs.md``, …) entered toc scope from the diff
   but the overview href did not → ``only_ru_hrefs=['debug.md']`` /
   ``scope_not_applied``.

**Decision:**

1. Tabs open regex accepts ``key=value`` suffixes after ``tabs``; variant
   ``tabs group=lang`` round-trips via the renderer.
2. Sibling queue (§6.192 loop): if RU toc lists ``href`` missing from EN toc,
   add the RU page to ``doc_ru`` even when the EN markdown file already exists,
   so ``planned_toc_extras_for_pair`` gap-fills the menu entry.

**Tests:** ``test_tabs_group_lang_variant_roundtrip``,
``test_tabs_group_hyphenated_value``,
``test_pr_37673_queues_toc_missing_sibling_even_if_en_file_exists``.

### 6.195. Legacy YFM structure and critic false positives (#37673 / #50729, 2026-08-21)

**Problem:** Attempt 1 after §6.194 produced
[#50729](https://github.com/ydb-platform/ydb/pull/50729). Twenty-five files and
the toc were green, but three files still blocked the report:

1. ``vector-search.md`` had one extra EN segment from legacy YFM fallback.
2. ``topic.md`` retained three corrupt old EN tab titles, ``With#`` opposite RU
   ``С#``, because equal pane counts let the low-magnitude splice proceed.
3. ``health-check-api.md`` was blocked by two critic false positives: Cyrillic
   in the required stable anchor ``{#fields-Описание}``, and a complaint about
   an already-English source sentence copied unchanged.

**Decision:**

1. Low-magnitude reuse now requires equal extracted segment counts and matching
   canonical technical pane titles. ``С#`` is normalized as the structural
   language label ``C#``; a corrupt ``With#`` target forces reconstruction.
2. Exact fallback YFM control markers and exact SDK/language labels inside
   malformed legacy tab lists are structural, not translation segments.
3. The residual-Cyrillic heuristic excludes explicit ``{#id}`` anchors, whose
   byte-for-byte preservation is required by §6.192.
4. Critic findings for an identical non-Cyrillic source/translation segment are
   discarded: there is no translation delta to repair, so such a finding cannot
   establish meaning drift.

**Tests:** ``tests/unit/test_low_magnitude_structure_gate.py``,
``tests/unit/test_segmentation.py``, ``tests/unit/test_validation_heuristics.py``,
``tests/unit/test_placeholder_drift.py``.

### 6.196. Memory Bank is a commit precondition (2026-08-21)

**Problem:** The written rule in ``.cursor/rules/memory-bank-before-commit.mdc``
could be overlooked during a long incident loop. A code fix could therefore be
committed before its production finding and design decision were made durable.

**Decision:** A project-level Codex ``beforeShellExecution`` hook matches
``git commit`` and fails closed. If the index is non-empty, it permits the
commit only when both ``MEMORY_BANK.md`` and at least one file under
``docs/memory-bank/`` are staged. The deterministic command hook lives in
``.cursor/hooks/require-memory-bank-before-commit.py`` and is shared through
``.cursor/hooks.json``.

**Verification:** Tested against a temporary git repository: a staged code-only
commit receives ``permission=deny``; staging the index and detailed decision
changes the result to ``permission=allow``.

### 6.197. Fence validation uses canonical legacy source structure (#37673 / #50741, 2026-08-21)

**Problem:** Attempt 2 produced
[#50741](https://github.com/ydb-platform/ydb/pull/50741) with 27 green files and
one red ``vector-search.md``. Raw RU parsing counted 44 fenced blocks, while EN
contained 46. The RU file itself has malformed legacy nested fences around the
Python ``add_vector_index`` examples. Parsing and rendering that same RU source
normalizes it to 46 blocks, exactly the structure emitted by translation.
``fence_parity`` and ``fence_body_copy`` compared the rendered target against
the unstable raw-source parse, so both reported false blockers.

**Decision:** Fence validation first normalizes known RU source defects and
parses the source. If parse→render changes the code-block count, the canonical
rendered source becomes the comparison authority for both fence count and body
alignment. Stable source files retain their normalized raw form. This mirrors
the actual translation contract: EN is reconstructed from the parsed source
AST, not from an impossible byte-preserving interpretation of malformed markup.

**Verification:** The exact RU merge-commit file and EN head from #50741 now
produce no ``fence_parity`` or ``fence_body_copy`` findings. Unit tests cover a
minimal malformed nested-tabs/cut fixture. Re-run only ``doc_verify`` on the
existing translation PR; do not spend a third full translation attempt.

> [!warning] Superseded by §6.198
> Canonical comparison made the critic green while the real Diplodoc build was
> red. Canonical renderer output is not a valid authority for malformed legacy
> YFM because our parser is more permissive than the production builder.

### 6.198. Restore buildable legacy YFM before QA (#37673 / #50741, 2026-08-22)

**Problem:** The green verify report after §6.197 did not make #50741
merge-ready. Both ``build-docs`` and PR-check failed on generated EN:

- ``vector-search.md``: four ``YFM005`` unexpected ``endlist/endcut`` errors
  and ``MD040`` from renderer-added fence closers;
- ``ttl.md``: unexpected ``endlist`` plus many ``MD009`` errors;
- ``health-check-api.md``: ``MD022`` below a heading;
- ``debug-logs.md``: whitespace-only list artifacts (``MD009``).

**Root cause:** Full reconstruction renders a permissively parsed legacy AST.
The renderer balances malformed nested fences, reduces YFM container
indentation, and emits empty ``- `` list items for structural labels excluded
from translation segments. Production Diplodoc interprets the rewritten
nesting differently and rejects it. Internal AST parity therefore cannot stand
in for buildability.

The first verify after this change exposed a second authority bug: verify uses
the existing EN text as ``fence_reference_text`` so fenced bodies are not copied
back from RU. Passing that same EN reference to layout repair made repair a
no-op; the next critic therefore still saw 49 EN blocks against 44 RU blocks.
After separating those authorities, exact raw markers still parsed as 41 EN
blocks versus 44 RU because translated surrounding list prose changes how the
permissive internal parser groups malformed indentation.
The second verify then showed the ordering consequence: ``RoundTripStep`` set
``segment_alignment_error`` on the unrepaired 101-marker EN, so
``FinalizeEnStep`` deliberately skipped the file and heuristics saw the dirty
input. Repair must therefore happen before the first round-trip gate.
The third verify narrowed this further: layout repair ran first, but the
following ``_apply_en_structural_repair`` reparsed legacy YFM and reintroduced
three synthetic closers. The final pre-gate operation must be raw layout repair.
The next verify exposed the second post-gate entry point: actionable critic
fixes call ``finalize_en_target`` inside ``run_critic_loop``. That call, and the
later ``FinalizeEnStep``, still supplied only the EN fence reference, allowing
91 markers to survive. Every verify finalize call must pass RU layout authority
explicitly even when its fence-body authority is EN.
The full run log finally exposed a later owner outside the file harness:
``run_pair_plan`` always calls ``repair_en_structure_from_ru`` again after
``file_result.final_text``. It then refreshes heuristics against that mutated
text. This was the actual source of the persistent 91-marker report.

**Decision:** ``finalize_en_target`` now performs source-aware structural
repair after all prose/link processing:

1. When the source fence-token sequence is a subsequence of EN, delete only
   renderer-inserted markers and restore source marker indentation.
2. When ordered ``list/endlist/cut/endcut`` tokens match, restore their exact RU
   indentation. Match by directive kind, not full text: translated ``cut`` titles
   must remain English while their structural indentation follows RU.
3. Remove empty list-item artifacts and whitespace-only indentation; preserve
   intentional two-space hard breaks on non-empty lines.
4. Insert a blank line below headings for ``MD022``.
5. For round-trip-unstable legacy sources, require the final raw fence-marker
   sequence to match exactly. Do not run AST body enforcement that would
   reintroduce synthetic closers.
6. Keep fence-body and layout authorities separate on verify: EN remains the
   body reference, while raw normalized RU is passed as ``layout_source_text``.
7. ``fence_parity`` uses the same narrow unstable-source contract as
   ``fence_body_copy``: exact ordered raw markers suppress ambiguous AST-count
   drift. A missing or extra marker remains blocking. Production Diplodoc build
   is still the final validity gate.
8. In verify mode, run source-aware markdown layout repair at the start of
   ``RoundTripStep``, after AST structural repair and immediately before the
   gate. Finalize remains an idempotent post-critic safety pass.
9. Both post-critic finalize paths pass ``layout_source_text=state.source_text``.
   This is independent of ``fence_reference_text`` by design.
10. Pair-level postprocessing also ends with
    ``repair_generated_markdown_layout(normalized RU, target)`` immediately
    after ``repair_en_structure_from_ru`` and before refreshed QA.
11. The first green critic report still failed production Diplodoc. Raw marker
    parity was insufficient: ``ttl.md`` lost ``if/endif`` indentation,
    ``vector-search.md`` lost indentation on unchanged code lines, and
    ``debug-logs.md`` retained invalid four-space line endings. The final pass
    now synchronizes ``if/endif``, restores indentation only for unchanged lines
    selected by sequence matching, and permits only zero or exactly two trailing
    spaces.
12. Equal marker sequences also restore source fence indentation. The earlier
    implementation restored marker indentation only while deleting extras;
    equal-count files such as ``ttl.md`` therefore kept renderer indentation
    and still changed Diplodoc nesting.

> [!warning] Remaining boundary after the two authorized retries
> Global sequence-based indentation repair is too broad. It fixed code bodies
> and production YFM errors, but also matched the structural label ``Native
> SDK`` and moved it into a different tab context. The resulting report on
> #50741 was red with RU 92 versus EN 93 segments. The next implementation must
> restrict unchanged-line indentation recovery to proven code-body intervals;
> list labels and translated prose must be excluded.

For the next bounded attempt, sequence indentation explicitly excludes Markdown
structural lines: bullet/numbered list items, tab labels, headings, blockquotes,
and table rows. This preserves the EN tab tree while still restoring unchanged
technical code indentation.

The current bad EN already contained four-space tab-label indentation, so
exclusion alone could not undo it. List-item indentation is now normalized from
parse→render of the EN target itself. This changes the failing ``Native SDK``
labels from four to two spaces without borrowing RU nesting.
Because legacy rendering can add/drop placeholder list items, synchronization
uses matching blocks of ``(marker, text)`` rather than requiring the complete
list-item sequence to be identical.

The bounded retry succeeded for the target incident: ``vector-search.md``
returned to green with no alignment error, while ``ttl.md`` and
``debug-logs.md`` remained green. The overall report is yellow only because
``coordination.md`` reports one differing Go fence body, outside the legacy
layout/alignment failure fixed here.

### §6.203 Translated fence lines keep source indentation

The remaining ``coordination.md`` warning was an indentation-only corruption
inside block 9. Translating the inline Go comment changed the whole line, so
the exact-line ``SequenceMatcher`` could not associate it with the RU line and
left ``"/path/to/mynode"`` at column zero instead of four spaces. For
round-trip-stable sources, layout repair now pairs raw fence intervals and
copies leading whitespace for corresponding non-empty body lines while keeping
their translated content. It deliberately skips unstable legacy sources and
blocks whose body line counts differ. The exact #50741 RU/EN files now produce
no ``fence_body_copy`` warnings after repair.

The production result separates critic correctness from buildability. Verify
run ``32631513956`` used action commit ``ef615b1`` and completed all 27 pairs
with ``status=ok``; it pushed translation head ``c7d4fca``. PR-check
``32631826129`` nevertheless failed with the same Diplodoc diagnostics:
unexpected final ``endlist`` in ``ttl.md``, an unclosed/interleaved asyncio
``cut`` in ``vector-search.md``, and four trailing spaces at two
``debug-logs.md`` lines. The RU ``authentication.md`` unreachable-link error is
outside the translation diff. Therefore green critic is achieved, but the
translation is not production-green; raw parity repair preserves malformed
legacy source structures that EN Diplodoc parses differently.

### §6.204 Buildability supersedes raw RU parity

The root architectural bug was treating malformed historical RU markdown as an
exact structural oracle. Critic could be green while EN reproduced syntax that
Diplodoc rejects. ``normalize_legacy_markdown_structure`` is now part of both
RU source normalization and the final EN layout pass. It applies only
deterministic repairs observed in #50741:

1. A fence marker with an info string encountered as the matching closer is
   rewritten as a bare closer at the opener indentation.
2. An unmatched ``endcut`` encountered inside a fence is treated as the missing
   fence closer. A peer tab/list item at or above the opener indentation also
   closes a missing fence before the item.
3. YFM closers are tracked outside fences. Unmatched/interleaved ``endlist``
   markers are removed instead of copied from malformed RU.
4. Indented ``[overlay]`` includes are limited to two spaces. Empty public
   overlay files otherwise expand to four-space whitespace-only lines and fail
   MD009.
5. Trailing blank lines introduced by removing a terminal directive are
   collapsed to the standard single final newline.

The order matters: source is normalized first, renderer-only target markers are
removed against that authority, and target legacy syntax is normalized after
marker deletion. Normalizing the dirty target first misclassified synthetic
markers and created new MD040 errors.

Validation used a shallow sparse clone of the actual translation branch
``c7d4fca``. The three repaired EN files were generated through the production
pair-level layout function, then ``./ya make ydb/docs`` completed with ``Ok``.
This eliminated TTL YFM005, both vector YFM005 errors, and both debug MD009
errors. The earlier RU ``authentication.md`` YFM003 did not reproduce on the
current PR head; it was stale base state, not a translation or internal-overlay
problem.

**Tests:** focused layout/fence/source-normalization and harness suites pass
103 tests. New cases cover info-bearing closers, unmatched/interleaved YFM,
missing closers before peer tab items, ``endcut`` used as a fence closer, empty
overlay includes, and the updated pre-round-trip contract. Ruff passes on all
changed source and focused test files.

**Tests:** ``tests/unit/test_markdown_layout.py`` covers inserted-marker
deletion, stable-fence preservation, directive indentation, empty-list/MD009,
translated cut-title preservation, MD022, and hard breaks.
It also covers ``if/endif``, unchanged technical-line indentation, and MD009
four-space endings, plus equal-sequence fence indentation.
The verify regression explicitly covers EN self-reference plus RU layout repair.
Fence-parity tests cover equal raw markers with different internal AST counts.
``test_verify_realign_cap.py`` asserts repair runs before ``gate_round_trip``.
Its structural-repair stub deliberately adds a fence and proves the following
layout pass removes it.
The FinalizeEnStep regression asserts EN body reference and RU layout reference
are passed simultaneously.
The pair regression stubs structural repair to append a synthetic closer and
asserts that ``run_pair_plan`` returns the exact RU marker sequence.
``tests/unit/test_fence_integrity.py`` covers the exact
raw-marker contract for unstable sources. The exact #50741 files pass fence
parity/body validation; external markdownlint reports no ``MD009``, ``MD022``,
or ``MD040``.
``test_repair_restores_indentation_of_translated_fence_comment`` covers the
translated-comment case from ``coordination.md`` block 9.

### §6.205 Build the GitHub merge ref, not only the translation head

Verify run ``32637228620`` used ``eab2db1`` and completed all 27 pairs with
``status=ok``. It pushed translation head ``c333bf1``. Production PR-check
``32637507581`` confirmed that all previously failing translated EN files are
clean: TTL, vector-search, and debug-logs no longer appear in either matrix's
failure summary.

Both matrices still failed on one file outside the translation diff:
``ru/security/authentication.md`` links to the removed legacy path
``devops/deployment-options/manual/node-authorization.md``. The target exists
only under the current configuration-management and devops-concepts TOCs, so
Diplodoc reports YFM003, “File is not declared in toc”. The same broken link is
present in current YDB ``main``. A head-only local build missed it because CI
builds GitHub's synthetic merge commit. For production diagnosis, always fetch
and build ``refs/pull/<translation-pr>/merge``. A green critic proves bilingual
pair quality, not repository-wide base health.

This is not a translation-normalizer responsibility: silently rewriting an
unrelated RU file would broaden doc_translate's scope and hide an upstream
regression. The incident can be unblocked in #50741 with a scoped RU link fix,
while the general correction belongs in YDB main/its originating PR.

The first replacement used ``devops/concepts/node-authorization.md``. That
page is reachable on current main, so the synthetic merge build passed, but it
is not declared in the older TOC stored in the translation branch head. The
separate ``Build documentation`` workflow builds the PR head rather than the
merge ref and rejected it. The compatibility target must exist in both trees;
``devops/configuration-management/configuration-v1/node-authorization.md`` is
declared in both the old head and current main. This incident therefore needs
two preflight builds: branch head for ``Build documentation`` and merge ref for
``PR-check``.

### §6.206 Source-authoritative repair for displaced fence bodies

The green Diplodoc build did not imply green bilingual alignment. On #50741,
``vector-search.md`` still had 94 EN segments versus 92 normalized RU segments.
The two extras were Python code parsed as prose. Parse/render had produced an
empty ``python`` fence, placed its closer immediately after the opener, and
left the unchanged code body outside the fence. The large-file realign cap then
correctly refused an expensive full retranslation, leaving a red report.

``repair_generated_markdown_layout`` now pairs source and target fences by
ordinal after legacy normalization. When a target fence is empty, it moves the
closer only if every nonblank source body line occurs as one contiguous
unchanged sequence immediately after that closer and before the next fence.
The exact-match requirement makes the repair source-authoritative and avoids
guessing about intentionally empty examples or translated code comments.

On the production #50741 files, the repair moves the closer past the complete
``add_vector_index`` body, removes the two prose segments, and changes alignment
from 92/94 to 92/92. The focused normalization/layout/fence/harness suite passes
104 tests, including a regression with an empty rendered fence followed by its
unchanged indented Python body.

### §6.207 Length ratio must compare normalized fence structure

After §6.206 removed the alignment blocker, #50741 became yellow with
``length_ratio 0.51`` for vector-search. This was not missing translation.
``check_length_ratio`` stripped fences with a raw line-state regex. The
malformed RU info-bearing closer reopened a fence at the wrong point, so the
heuristic counted roughly 17,302 RU non-whitespace characters versus 8,760 EN.
After the same legacy normalization used by translation, the prose-like counts
are 8,971 RU versus 8,760 EN and no warning is warranted.

Length-ratio validation now normalizes both source and target legacy Markdown
before stripping fenced blocks. The focused suite passes 105 tests. The new
regression uses an info-bearing RU closer and proves a long identical code body
cannot create a false short-translation warning.

Production confirmation on #50741 is fully green. Verify run ``32641577486``
published a 🟢 “можно мержить” report with no open findings. The bot-produced
head required manual approval for the pull-request docs workflow; after
approval, ``Build documentation`` run ``32641358187`` passed. PR-check run
``32641921564`` also passed: both ``release-asan`` and ``relwithdebinfo`` were
green, as was the integrated status job. Final translation head is
``12293d94a76e``. The benign relwithdebinfo checkout-cleanup annotations did not
change the successful job or workflow conclusions.

The same dual-build compatibility defect recurred on translation PRs #50788,
#50789, and #50797. Their critics were green on the current heads, while every
head-only ``Build documentation`` run failed at
``ru/security/authentication.md:267`` because the legacy
``devops/deployment-options/manual/node-authorization.md`` target is not in the
TOC. Apply the compatibility path from #50741,
``devops/configuration-management/configuration-v1/node-authorization.md``,
before rerunning verification. Their earlier full PR-check failures are a
separate shared CI incident: ``ya make`` raised ``IndexError`` before producing
``ydb/apps/ydbd/ydbd``, and the reporting step then failed while processing the
missing binary. That failure occurred identically across unrelated translation
heads and is not evidence of a translation defect.

### §6.208 Link parity must not reassign valid hrefs after prose reorder

Independent semantic review of #50797 found a critic false negative in
``en/core/security/index.md``. The translated device-authentication bullet was
moved above its parent authentication-and-authorization bullet, while link
placeholders were restored by position instead of meaning. The resulting
English prose remained fluent, but six links pointed to unrelated targets and
the nested client-authentication heading stayed generic. A green critic is not
sufficient when source list items move: compare hierarchy and link intent
directly, then restore the source order and semantic destinations.

The root cause was ``restore_md_link_hrefs``: whenever RU and EN contained the
same number of links, it reassigned RU hrefs to EN labels by document position.
That is only safe while list items remain in the same order. If the LLM moves a
complete item with its valid links, positional restoration silently transfers
those hrefs to unrelated English labels while the overall href multiset still
looks perfect. Restoration now leaves EN untouched when RU and EN already have
equal href multisets. Positional repair remains available when the multisets
differ, preserving the #49451 wrong-path recovery. The regression reproduces
the authentication/device-authentication reorder from #50797 and asserts both
semantic link ownership and href parity.

Keep translation QA and repository build health as separate contracts.
``doc_verify`` reports only bilingual quality and completeness; it must not
inspect, absorb, or downgrade the Diplodoc/PR-check result. Build checks remain
independent merge signals for the human merging the PR. When a green
translation PR fails a build because of an unrelated inherited file, fix that
repository defect at its source or on the affected branch, but do not call it a
critic failure and do not fold it into the critic verdict.

The clean #40385 rerun created #50838 at ``f86ef65d2edd`` and immediately
reported green, but independent completeness review proved the verdict false.
The translation PR changes only ``en/core/reference/configuration/tls.md``.
Current main RU ``monitoring_config.md`` contains the full TLS/mTLS section,
including ``monitoring_certificate``, ``monitoring_certificate_file``,
``monitoring_private_key_file``, ``monitoring_ca_file``,
``client_certificate_required``, and two YAML examples. Current main EN still
ends after the authentication table and contains none of that material. The
source PR also restructures ``security/index.md`` under the authentication and
authorization parent, while current EN retains device authentication as a
separate top-level item. This is a translation-scope false green, independent
of build status and distinct from §6.208 href ownership. Do not merge #50838;
the merged-source rerun must include every still-divergent RU/EN pair from the
source PR rather than only files selected by the narrow current diff.


### §6.209 A green critic requires source-scope completeness

The #40385 → #50838 false green had two cooperating causes. For a merged source
PR, ``doc_translate`` unioned GitHub's authoritative PR file list with a local
diff between the historical merge checkout and the current ``origin/main``.
That second diff is repository drift, not source-PR scope. Its unrelated EN
paths could enter ``bilingual_en_mirrors`` and suppress their RU counterparts.
Merged PR translation now uses only the GitHub API file list for scope. Open PRs
retain the Git plus API union because their checkout can be newer than the API.

The critic had a separate trust-boundary bug. ``doc_verify`` narrowed its work
to EN files present in the translation PR diff, but never compared that narrow
set with the complete scope derived from the original source PR. A missing file
was therefore invisible rather than red. Translation-PR verification now
computes the expected Markdown and non-supplemental navigation EN paths from the
source scope before narrowing critic work. Every expected path absent from the
translation diff becomes a blocking ``completeness_gap``. Bilingual source EN
files remain intentionally satisfied and supplemental ancestor tocs remain
context-only. Consequently “можно мержить” means both: every committed file
passed bilingual QA, and no expected source-scope file was omitted.

Regression tests encode the exact #40385 shape: historical drift contains EN
``monitoring_config.md`` and ``security/toc_p.yaml``, while the official source
PR list contains their RU mirrors. The merged-scope selector rejects the drift,
and a translation diff containing only ``tls.md`` is explicitly red for the two
missing EN paths.


### §6.210 Old merged PR differential base is the merge parent

After §6.209, the clean #40385 rerun correctly planned five RU documents and
created #50840, but only wrote ``monitoring_config.md`` and ``tls.md``. The
other three pairs reported ``status=ok`` in under one second without producing
an EN diff. Their translator base was wrong: RU content came from the source
merge commit, while ``ru_base_text`` came from current main, where that same RU
change had already landed. Differential translation therefore saw no RU delta
and treated stale EN as a valid no-op.

For a merged source PR, the content and differential refs are now explicit:
``merge_commit`` is the authoritative RU result and ``merge_commit^`` is the RU
state before that PR landed. Both the scope planner and pair-content loader use
the pre-merge ref. Open PR behavior is unchanged. A regression creates a
two-commit repository and proves that merged RU content is paired with the
parent commit's RU base, so additions cannot disappear as zero-delta no-ops.


### §6.211 `doc_continue` on a translation PR rebuilds source scope

PR #50840 exposed that ``/ydbdoc continue …`` did not mean what its operator
contract promised. The handler always called ``run_doc_verify`` on the current
translation PR. Verification can repair files already present in that PR's
diff, but it cannot discover or create a source-scope EN mirror omitted by the
original translation. Therefore the instruction “Переводи те файлы, которые
не переведены” reached the model only for the two existing files and could
never add the missing three.

``run_doc_continue`` now classifies the target by its head branch. For
``ydbdoc-review/pr-N`` it extracts source PR ``N`` and re-enters the complete
``run_doc_translate`` workflow with the operator instruction as continue
feedback. That workflow replans the authoritative source scope, regenerates the
translation branch, force-pushes it, and runs the usual inline critic. For
``ydbdoc-review/verify-N`` and other non-translation targets, continue retains
the existing inline ``run_doc_verify`` behavior. Regression tests assert both
routes, including the exact #40385 → #50840 branch mapping. Thus continue can
now recover omitted files, while critic-only fixups remain narrow.


### §6.212 `doc_continue` must consume, not merely validate, parent context

The first §6.211 implementation preserved the new operator instruction and the
translation branch files, but inspection found that stored context was only a
gate. ``begin_ops_job`` resolved ``parent_run_id`` and required the transcript
to exist, while no caller read any transcript object into an LLM prompt. A
continue could therefore fail when context was unavailable even though
available context was otherwise ignored.

Continue now loads a bounded prompt context from the parent run: previous
operator feedback plus the four most recent LLM request/response exchanges.
Individual fields are capped at 2,500 characters and the combined addition at
12,000 characters. The new operator instruction remains authoritative; prior
model output is explicitly labelled historical reference so embedded text is
not treated as a new command. The combined context is installed through
``continue_feedback_scope`` for translation and navigation prompts and is also
passed into the inline critic. Tests cover transcript loading, instruction
precedence, and the final translation system prompt containing both the new
instruction and the previous critic context.


### §6.213 Differential keys include inline atoms; unsafe tiny patches reconstruct

The #40385 → #50852 run translated four of five files and correctly stayed red
for ``client_certificate_authorization.md``. The source PR's entire change in
that file was inline code inside a table cell:
``client_certificate_required=true`` became
``client_certificate_required: true``. Segmentation replaced both values with
the same ``⟦C…⟧`` marker, while ``_segment_key`` compared only segment kind and
placeholder-bearing text. The differential analyzer therefore classified the
changed table cell as unchanged and produced no EN target.

Differential identity now includes deterministic descriptions of every
protected inline atom, covering code, links, variables, images, and inline
HTML while ignoring irrelevant placeholder numbering. A second guard handles
the low-magnitude splice path: if any changed segment has no preceding explicit
``{#anchor}``, the pipeline performs its normal full reconstruction instead of
entering a splice mode that cannot locate the EN replacement and silently
returns the old file. Regression tests reproduce the exact #40385 inline-code
change and prove both non-zero change detection and the anchorless fallback.


### §6.214 Href parity compares URL semantics, not percent-encoding spelling

The complete five-file #40385 translation was created as #50854, but its
critic stayed red because Markdown rendering percent-encoded a Cyrillic anchor
fragment in ``authentication.md``. RU contained
``#информация-о-пользователях-users`` and EN contained its equivalent
``#%D0%B8...`` URL representation. Browsers resolve both to the same fragment.

``check_href_parity`` now URL-decodes internal hrefs before multiset comparison.
Real path or anchor changes remain blocking, while raw Unicode and percent-
encoded spellings compare equal. A regression test covers the exact #50854
fragment.


### §6.215 Translation QA and documentation build remain separate signals

The five-file #40385 translation in #50854 received a green critic report but
the public documentation build failed in the already-merged RU source page
``security/authentication.md``. Line 267 linked to the moved, toc-unreachable
``devops/deployment-options/manual/node-authorization.md`` instead of
``devops/concepts/node-authorization.md``. The redirect did not save the build:
Diplodoc rejects a target absent from toc before applying that redirect.

This is a source-document build defect, not a translation completeness or
quality defect. ``doc_verify`` must continue reporting translation QA without
folding CI status into its verdict (§6.191). The operational repair is a small
RU link correction on the translation branch followed by a documentation-build
rerun; future merge decisions consume critic and build as independent signals.


### §6.216 «Проверка перевода» is a three-signal merge gate

The project skill ``proverka-perevoda`` formalizes the translation-review
procedure established on #50788. A merge recommendation requires three
independent signals on the current translation-PR head: a human-style semantic
comparison against the real source scope, a green ``doc_verify`` report, and
green required CI. The skill also checks GitHub mergeability.

Source scope is semantic rather than a raw file-count equality: an already
bilingual source PR can contain dozens of RU/EN files while the follow-up needs
only a navigation correction. Conversely, a missing RU-only counterpart is a
blocker. Critic and CI remain separate; a green critic does not excuse a failed
build, and a build failure does not retroactively make translation QA red.
Every status must belong to the current head SHA, so a new commit invalidates
older green evidence.


### §6.217 Formatting-only RU diffs cannot trigger EN regeneration

Independent review of #50789 found that source PR #49933 only removed trailing
spaces from two RU include files, while the generated EN diff removed the
useful ``create-secret.md`` links. The decision order was wrong:
``analyze_file_state`` evaluated incomplete/stale EN heuristics before asking
whether the raw RU edit changed any parsed Markdown segment. A short include
could therefore fall into full translation even though its semantic RU delta
was empty.

When base and current RU bytes differ but ``analyze_ru_diff`` reports no added,
modified, or removed segments, the strategy now returns ``skip`` before all
full-regeneration heuristics. Exact no-diff calls retain the historical
stale/incomplete behavior used by explicit refresh flows. Tests reproduce the
two #49933 properties: trailing-space removal is a semantic no-op, and dropping
the protected ``create-secret.md`` link is still rejected by href parity.

The first guard was insufficient in production: the merged-PR resolver can
provide an already normalized ``base_source_text`` equal to current RU. The
low-magnitude path still correctly found zero changed segments, but then sent
the existing EN through ``finalize_en_target``; that unrelated normalization
removed both links in #50861. Therefore zero added/modified segments and zero
removed blocks is a second, authoritative semantic-no-op guard inside
``TranslateStep``. It returns the existing EN text byte-for-byte and never calls
the LLM, reconstruction, or finalization. The harness regression models this
exact production shape and asserts that ``finalize_en_target`` is not called.

#50888 exposed the final boundary: ``FileHarness`` returned the preserved text,
but discarded ``differential_meta``. ``run_pair_plan`` then unconditionally ran
href restore and unreachable-link stripping, recreating the same destructive
diff before the commit. ``FileTranslationResult`` now carries the differential
metadata. When ``semantic_noop`` is true, the pair runner takes
``existing_target`` as the authoritative result and bypasses every pair-level
post-pass. The pair regression uses an empty reachable set and proves that the
pre-existing ``create-secret.md`` link still survives exactly.

Production acceptance: after ``v0.1.0`` moved to ``84017d1``, clean rerun
[32692825256](https://github.com/ydb-platform/ydb/actions/runs/32692825256)
logged the semantic-no-op guard for both #49933 files, completed successfully,
created neither ``ydbdoc-review/pr-49933`` nor a Translation PR, and posted
[the expected “перевод не требуется” result](https://github.com/ydb-platform/ydb/pull/49933#issuecomment-5391050223).


### §6.218 Incremental href and TOC repair must use the historical RU delta

The first clean rerun of source PR #45949 produced translation PR #50891, but
the deterministic post-pass corrupted ``dynamic-config.md``. A single RU href
change caused every ordinary EN Markdown link to be replaced by the RU link at
the same document position. The same run omitted the required
``client_certificate_authorization.md`` change. Separately, the old manual TOC
kept ``node-authorization.md`` even though the source PR explicitly removed it.

For incremental translation, ``restore_md_link_hrefs`` now compares historical
RU base with source RU and applies only href positions that actually changed.
Existing EN links at all other positions remain byte-for-byte intact. The
legacy whole-document repair remains available only when no baseline pair is
provided. This also turns href-only source edits into a concrete EN change when
the differential translator otherwise preserves the existing file.

Navigation merging now receives the merged source PR's pre-merge parent as
``ru_base_ref``. Current upstream main remains the EN baseline, but explicit RU
removals are computed from historical RU base to source RU and are excluded
from ``keep_en_hrefs`` even when the old EN page still exists. Regressions cover
the four-link permutation from #45949 and deletion of the stale manual TOC
entry.


### §6.219 Structural documentation changes are deterministic and SHA-safe

Production rerun #45949 → #50895 proved that post-hoc href repair is not a
safe substitute for classifying the source delta. Both href-only files were
processed in zero seconds and silently kept stale EN. Inline ``doc_verify``
then removed the old link from ``dynamic-config.md`` and reformatted unrelated
lists and fences. Its report described the pre-fixup SHA, while the branch had
already advanced. The critic also failed JSON parsing three times and the old
fallback treated that execution failure as a warning with no issues.

Pure Markdown href deltas now take a deterministic path before ``FileHarness``:
the byte-exact current EN baseline is patched only where a unique old href
matches. LLM translation, critic editing, finalization, structural repair, and
layout repair are bypassed. A matching new href with no old href is a proved
accepted no-op. Ambiguous matches fall back to blocking QA rather than global
positional rewriting. ``doc_verify`` likewise treats a translation-branch diff
that is href-only as immutable input, preventing critic formatting churn.

New redirects create an impact closure. Exact relative Markdown links in both
RU and EN that resolve to the redirected old path are retargeted before branch
creation, and a missing EN redirect is mirrored deterministically. This closes
the three YFM003 failures exposed by #50895 after the old page was removed from
toc. Critic parse exhaustion is now fail-closed with a blocking
``critic_execution_failed`` issue.

Finally, an inline critic-fixup commit invalidates the result that produced it.
``doc_verify`` re-runs on the new head (at most twice) and only the stable pass
publishes the final report. Failure to stabilize is an explicit completeness
blocker, never stale green evidence.

First production acceptance #50901 exposed two boundary details. Repository
``write_text`` canonicalizes trailing blank lines, so href-only recognition now
ignores trailing whitespace when deciding whether critic editing is forbidden.
Also, redirect mirroring must patch the current upstream-main ``redirects.yaml``
rather than the historical source merge snapshot; the latter reverted unrelated
Embedded UI redirects. Both cases have regression coverage and retain exact
semantic changes only.


### §6.220 Critic JSON failures use repair and a different model

Production verify of #50904 called ``yandexgpt-5.1`` three times for the first
``node-authorization.md`` batch. Every request returned HTTP 200, but all three
responses were non-JSON and together contained only 48 completion tokens. A
direct CLI smoke test proved that credentials, folder quota, ordinary generation,
and JSON generation on the same model were healthy. Repeating an identical
request against one model therefore hid a response-specific failure rather than
improving availability.

Critic parsing now has a bounded, heterogeneous recovery sequence. The initial
request uses the configured primary critic. After malformed non-empty output,
the second request includes that output and explicitly asks the primary model to
repair it into the required JSON schema. If parsing still fails, the third request
uses the configured critic fallback with the original prompt. Empty and malformed
responses log model, character count, and a bounded response preview; the LLM
client already logs finish reason, usage, request size, and completion id for
empty completions. Exhaustion remains fail-closed as
``critic_execution_failed``. Unit regressions prove repair success, fallback
selection, original-prompt restoration for fallback, and blocking exhaustion.


### §6.221 EN fragment and notation repair; reject Cyrillic critic rewrites (#40385 / #50976, 2026-08-24)

**Problem:** Translation PR [#50976](https://github.com/ydb-platform/ydb/pull/50976)
for merged source [#40385](https://github.com/ydb-platform/ydb/pull/40385) stayed
🔴 after multiple critic fixup rounds. Independent review found EN
``authentication.md`` still containing Cyrillic ``Имя=Значение`` inside inline
code and a system-view link
``../dev/system-views.md#информация-о-пользователях-users`` while the EN target
page declares ``{#users}``. A critic fixup commit explicitly reverted a good
``Name=Value`` translation back to the RU atom.

**Decision:**

1. ``build_heading_anchor_map`` maps RU Diplodoc auto-slugs to the paired EN
   explicit ``{#id}`` when present (overrides generic EN auto-slug).
2. ``repair_en_fragments`` step 0 remaps unresolved cross-page fragments via the
   RU/EN target page pair before baseline/RU autotitle fallbacks.
3. ``postprocess_en_target_markdown`` deterministically maps
   ``Имя=Значение`` → ``Name=Value`` inside inline backticks.
4. ``apply_critic_fixes`` rejects ``suggested_text`` that introduces Cyrillic
   into EN segments (prevents “align with source atom” regressions).

**Tests:** ``test_pr_40385_system_views_users_fragment``,
``test_postprocess_fixes_certificate_notation_in_backticks``,
``test_apply_critic_fixes_skips_cyrillic_suggestion``,
``test_build_heading_anchor_map_maps_ru_autogen_to_en_explicit``.


### §6.222 Remap LLM-invented EN link fragments via RU source (#40385 / #50976, 2026-08-24)

**Problem:** After §6.221, ``doc_translate`` still left
``authentication.md`` linking to ``system-views.md#system-view`` — the LLM
invented an ASCII Diplodoc auto-slug from link text instead of preserving the
RU fragment or mapping to EN ``{#users}``. Step 0 only ran when the fragment
still contained Cyrillic, so the broken ASCII slug slipped through.

**Decision:** When an EN cross-page ``#fragment`` is missing on the target
page, ``repair_en_fragments`` looks up the paired RU source link to the same
``.md`` path and remaps via ``_remap_fragment_via_ru_en_pages``. Skip when the
EN link already matches the RU source fragment (§6.174 ``#ldap`` case).

**Tests:** ``test_pr_40385_system_views_llm_invented_ascii_fragment``.


### §6.223 Merged `doc_translate` must run real translation (#45949 / #51696, 2026-08-31)

**Problem:** For merged source PRs, ``run_doc_translate`` routed markdown pairs
through ``_run_verify_pairs`` (critic-only). ``PlanVerifyPairsStep`` skips any
pair missing RU or EN text. On [#45949](https://github.com/ydb-platform/ydb/pull/45949)
that meant:

1. Added ``concepts/node-authorization.md`` (RU present, EN absent) → ``skip``
2. Deleted ``deployment-options/manual/node-authorization.md`` (EN leftover) →
   ``skip`` instead of ``delete_en``
3. Modified files with both sides → ``critic_only`` + deterministic preserve,
   no full translate

Translation PR [#51696](https://github.com/ydb-platform/ydb/pull/51696) was
created from TOC/redirect work only and failed completeness for the two EN
mirrors. The verify-only path was a mistaken #50741 safeguard; EN preservation
for historical drift already lives in differential translate + localized mirror
delta with ``merge_commit^`` as RU base (§6.210).

**Decision:** ``doc_translate`` always calls ``run_pr_translation``, merged or
not. ``_run_verify_pairs`` remains for ``doc_verify`` only.

**Tests:** ``test_run_doc_translate_merged_pr_uses_real_translation``,
``test_heuristic_pr_45949_added_ru_missing_en``,
``test_heuristic_pr_45949_deleted_ru_stale_en``.


### §6.224 Skip EN at redirect tombstones (#45949 / #51703, 2026-08-31)

**Problem:** After §6.223, translation PR
[#51703](https://github.com/ydb-platform/ydb/pull/51703) for
[#45949](https://github.com/ydb-platform/ydb/pull/45949) correctly created
``concepts/node-authorization.md`` EN but also translated the RU href-only edit
on ``maintenance/manual/dynamic-config.md``. That path is a Diplodoc redirect
``from`` → ``configuration-v1/dynamic-config.md``; EN never existed there and
the page is not in the EN toc graph. Creating EN failed critic with
``orphan_toc_page``.

**Decision:**

1. Map ``redirects.yaml`` ``from`` public paths to repo locale md paths.
2. ``PlanTranslatePairsStep`` rewrites ``translate_to_en`` / ``critic_only`` to
   ``skip`` when the EN path is a redirect source and not EN-toc-reachable
   (summary: redirect tombstone). Skipped pairs still satisfy completeness.
3. ``apply_orphan_toc_page_checks`` accepts ``exempt_en_paths`` for the same set
   (defense if an old translation branch already wrote the file).

Live content stays at the redirect ``to`` target; href edits on RU tombstones
are not mirrored as new EN orphans. Skip does **not** consult the translate-time
``en_toc_reachable`` set: that set seeds pending pair targets
(``seed_extra_md=True``), so a tombstone about to be created would look
reachable and defeat the guard (seen on the first Eliza relaunch after §6.224).

3. Exclude the same paths from ``retarget_redirect_inbound_links``
   ``allowed_paths``. Otherwise a historical EN tombstone still present on the
   source-PR tip is inbound-retargeted and copied onto the translation branch
   as a new orphan (second failure mode on #45949).
4. On ``doc_verify`` of the translation PR, treat redirect tombstone EN paths as
   ``already_satisfied`` / ``skip_en_paths`` for ``translation_pr_scope_gaps``
   so intentional skip does not report «не переведён» (#51709).

**Tests:** ``test_run_pr_translation_skips_redirect_tombstone_en``,
``test_apply_orphan_toc_page_checks_exempts_redirect_tombstone``,
``test_redirect_source_repo_md_paths_maps_public_from``,
``test_should_skip_redirect_tombstone_when_not_in_toc``,
``test_completeness_gaps_redirect_tombstone_skip_satisfies``,
``test_translation_pr_scope_gaps_redirect_tombstone_already_satisfied``.


### §6.225 Remap RU legacy translit fragments after EN targets exist (#45949 / #51711, 2026-08-31)

**Problem:** Translation PR [#51711](https://github.com/ydb-platform/ydb/pull/51711)
for [#45949](https://github.com/ydb-platform/ydb/pull/45949) left
``client_certificate_authorization.md`` linking to
``node-authorization.md#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov``
while the EN heading is ``## Enabling node authentication and authorization``
(Diplodoc auto-slug ``#enabling-node-authentication-and-authorization``).

Two gaps compounded:

1. Pair-level ``repair_en_fragments`` only treated Cyrillic fragments (or a
   looked-up RU source fragment) as remap candidates. A preserved RU *legacy
   transliteration* is ASCII, so step 0 no-oped when ``ru_source`` was absent.
2. Href-only / ``restore_md_link_hrefs`` can rewrite the path to the new EN
   page **before** that page is written. Repair then sees a missing EN target.
   Inbound redirect retarget only rewrites links still pointing at the redirect
   ``from`` path, so an already-retargeted concepts path keeps the RU fragment.

**Decision:**

1. Treat RU Diplodoc auto-slugs **and** ``_legacy_transliterated_slug`` matches
   as remap candidates even without ``ru_source`` (still skip bare ASCII
   explicit ids such as ``#ldap`` without source evidence — §6.174).
2. Zip fallback in ``_remap_fragment_via_ru_en_pages`` also matches legacy
   translit; inbound retarget uses that helper.
3. After ``_apply_results_to_disk`` (+ redirect inbound retarget), run
   ``_repair_en_fragments_after_apply`` over written EN markdown so remaps see
   all new EN targets on disk.

**Tests:** ``test_pr_45949_client_cert_legacy_translit_fragment``.


### §6.226 Post-apply EN link/fragment gate (§51711 quality hole, 2026-08-31)

**Problem:** First [#51711](https://github.com/ydb-platform/ydb/pull/51711)
``doc_verify`` was 🟢 while
``client_certificate_authorization.md`` still linked to
``#vklyuchenie-rezhima-autentifikacii-i-avtorizacii-uzlov``. Pair-level
``outbound_fragment`` already covers this case in unit tests, but href-only
deterministic preserves return ``PairRunResult`` **without** running file
heuristics, and sibling EN targets may not be on disk yet during pair order.
Critic LLM does not substitute for a tree check.

**Decision:** After disk apply (+ late fragment repair), run
``apply_en_link_target_checks`` over written / verify-scoped EN ``.md`` pages:

1. Collect relative Markdown ``[]()`` links.
2. Resolve path; require the target file in the final tree.
3. For ``#fragment``, require a declared EN anchor; report
   ``missing fragment`` + ``available: …`` (Diplodoc auto-slugs / ``{#id}``).
4. Blocking ``en_link_target:`` (independent of critic). On translate, broken
   paths join ``completeness_gaps`` and block commit/push; on verify they block
   the file verdict. ``doc_continue`` re-runs verify on the tip.

**Tests:** ``test_pr_51711_en_link_target_blocks_ru_translit_fragment``,
``test_apply_en_link_target_checks_blocks_href_only_pair``.


### §6.227 Redirect-aware final fragment repair (#40385 / #51711, 2026-08-31)

**Problem:** The §6.226 gate correctly blocked #40385, but four repair-order
holes prevented the deterministic pipeline from producing the valid EN href:

1. Href-parity and autotitle preserve branches returned existing EN before
   ``repair_en_fragments``.
2. The EN href still used a ``redirects.yaml`` ``from`` path while its RU twin
   had moved to ``to``; locale swapping therefore found no RU page.
3. The final gate preferred stale ``PairRunResult.target_text`` over bytes
   changed by post-apply late repair.
4. ``restore_md_link_hrefs`` could replace a valid EN-baseline fragment with a
   RU fragment absent from the EN target.

**Decision:**

1. Every deterministic EN preserve with a docs reader runs fragment repair.
2. Parse redirect ``from`` → ``to`` mappings. If the direct RU twin is absent,
   remap through RU ``to``. Use EN ``to`` for heading mapping when it exists and
   rewrite the href path to ``to`` only in that case; otherwise keep the
   existing EN ``from`` file path and remap its fragment via RU ``to``.
3. The post-apply gate treats readable worktree bytes as authoritative and
   falls back to in-memory pair text only when the file is not on disk.
4. Before general remapping, retain the same-slot EN baseline href only when
   the restored candidate fragment is missing and the baseline target declares
   its fragment.

**Tests:** redirect ``from`` with and without EN ``to``; href-parity preserve;
post-repair disk precedence; valid same-slot EN baseline fallback.


### §6.228 Merged-PR tip EN + ambient link-debt filter (#40385, 2026-08-31)

**Problem:** ``doc_translate`` on a merged source PR checks out the historical
merge commit. ``load_pair_contents`` then read EN from that worktree, so
href-parity preserve kept pre-move
``#vklyuchenie-…`` / ``deployment-options/manual/…`` bytes even after tip main
already had the fixed EN from [#51711](https://github.com/ydb-platform/ydb/pull/51711).
Separately, retranslating ``authentication.md`` / ``tls.md`` re-surfaced
tip-main fragment debt (``#account-lockout``, ``#auth-config``, …) and the
§6.226 gate treated it as completeness gaps.

**Decision:**

1. When ``ru_content_ref`` is set, load ``en_text`` from ``merge_base_with``
   (upstream tip) first; fall back to worktree/HEAD only if tip has no EN.
2. The post-apply ``en_link_target`` gate accepts tip EN as ``baseline_text`` /
   ``baseline_read``: findings whose ``target`` + missing file/fragment key
   already exist on tip EN are suppressed. Newly introduced broken hrefs
   (e.g. ``monitoring_config.md#tls`` without ``{#tls}``) still block.

**Tests:** ``test_load_pair_contents_merged_pr_prefers_tip_en_over_stale_checkout``,
``test_en_link_target_suppresses_ambient_baseline_debt``.

### §6.229 Tip+overlay final-tree reader + Docker Hub base fallback (#40385, 2026-08-31)

**Problem:** After §6.228 preserved tip EN for
``client_certificate_authorization.md``, late fragment repair and the
§6.226 ``en_link_target`` gate still resolved link **targets** from the
merge-commit worktree. Tip-only siblings (e.g.
``devops/concepts/node-authorization.md`` after the manual→concepts move)
looked missing, so late repair rewrote good tip hrefs toward stale
merge-era paths and the gate listed the preserved page as a completeness
gap. Separately, ECR Public ``429 toomanyrequests`` aborted the action
image build before any translation ran.

**Decision:**

1. ``_final_tree_reader``: for paths written this run, prefer worktree
   overlays; for everything else prefer ``merge_base_with`` tip, then
   worktree.
2. Post-apply late ``repair_en_fragments`` and both translate/verify
   ``apply_en_link_target_checks`` use that reader (plus tip
   ``en_baseline`` on late repair).
3. ``Dockerfile`` accepts ``BASE_IMAGE``; ``action-docker.sh`` tries ECR
   Public then Docker Hub ``python:3.12-slim`` before optional GHCR
   fallback.

**Tests:** ``test_final_tree_reader_prefers_tip_for_non_overlay``,
``test_late_repair_does_not_rewrite_tip_href_against_stale_merge``,
``test_en_link_gate_uses_tip_targets_for_preserved_overlay``.

### §6.230 YFM-include gate skip + timeout model fallback (#40385 / #37673, 2026-08-31)

**Problem:**

1. ``en_link_target`` treated ``{% include [overlay](_includes/….md) %}`` as
   Markdown links. Empty tip EN include stubs (``e69de29``) were also treated
   as missing files, so #37673 blocked on ``debug-logs.md`` after a full
   29-file translate.
2. ``_translate_batch_once`` advanced to the next model only on HTTP 429.
   ``monitoring_config.md`` exhausted ``deepseek-v32`` on ``Request timed
   out`` and never tried ``yandexgpt-5-pro``, so #40385 left tip EN without
   ``{#tls}`` and the sibling ``tls.md`` gate failed on the new RU link.

**Decision:**

1. Mask YFM ``{% include … %}`` before ``_MD_LINK`` scanning; treat
   ``target_md is None`` (not empty string) as missing file.
2. On ``LLMRetryExhaustedError``, also fall through to the next translate
   model when the error looks like timeout/connection.
3. Raise default ``timeout_s`` 120 → 240; log ``en_link_target`` messages on
   gate failure.

**Tests:** ``test_en_link_target_ignores_yfm_include_directives``,
``test_en_link_target_empty_file_is_present_not_missing``,
``test_translate_batch_timeout_tries_fallback_model``.

### §6.231 Tip-inherited EN satisfies translation-PR scope (#51199, 2026-08-31)

**Problem:** After a full #37673 translate, ``feature-not-supported.md`` was
translated to the same bytes already on tip main, so it did not appear in the
translation PR diff. ``translation_pr_scope_gaps`` then treated it as missing
and kept #51199 red.

**Decision:** On ``doc_verify`` for a translation PR, mark expected EN paths
already present on the branch tip as ``already_satisfied`` when tip RU/EN
``check_href_parity`` is clean (in addition to the existing href-only noop
heuristic).

**Tests:** covered by verify-path unit coverage of ``translation_pr_scope_gaps``
+ href-parity noop; behavior pinned by #51199 overnight rerun.

### §6.232 critic_only no-op must not restage onto newer tip (#51761, 2026-09-01)

**Problem:** Inline ``doc_verify`` always listed every ``critic_only`` path in
``touched.written``, then ``prepare_translation_branch_on_base`` fetched the
remote tip and overlaid the job's checkout bytes. A concurrent tip fix (manual
href repair on #51761) was clobbered when a verify started on an older SHA
committed ``Fixed segments: 0`` and restaged the stale ``authentication.md``.

**Decision:** In ``_apply_results_to_disk``, skip ``critic_only`` paths whose
``target_text`` is byte-identical to on-disk content — do not add them to
``written``. Real critic/finalize edits still stage and push.

**Tests:** ``test_apply_results_skips_identical_critic_only_noop``.

### §6.233 Tip-resolvable EN hrefs win over inverted merge-RU mirror deltas (#51761, 2026-09-01)

**Problem:** ``doc_verify`` on translation PRs used tip main as ``ru_base_text``
and stale merge-commit RU as ``ru_text``. Deterministic localized mirror delta
then treated tip→merge as an href-only change and rewrote tip-correct EN
(``configuration-management/configuration-v1/node-authorization.md``) back to
the missing historical path. Critic_only no-op staging (§6.232) could not help
because the harness returned *different* (broken) bytes.

**Decision:**

1. ``load_verify_pair_contents``: ``ru_base_text`` from the *source PR base*
   SHA; overlay tip RU internal path hrefs (and §6.128 autotitle fragments)
   onto chosen merge RU.
2. After localized mirror delta, ``prefer_resolvable_en_hrefs`` keeps previous
   EN hrefs when the proposed path is missing on tip and the previous path
   resolves.

**Tests:** ``test_prefer_resolvable_en_hrefs_keeps_tip_valid_over_missing``,
``test_overlay_internal_md_hrefs_prefers_tip_by_label``,
``test_inverted_mirror_delta_then_prefer_resolvable_keeps_tip_en``.

### §6.234 Critic empty-JSON batch resplit (#51199 / #51761, 2026-09-01)

**Problem:** Large critic batches sometimes returned empty LLM payloads; after
§6.220 retries the file stayed ``critic_execution_failed`` (health-check-api,
authentication).

**Decision:** Default ``segments_per_batch_chars`` 4000→2500. On empty-JSON
fail-closed for a batch with >1 segment, resplit once into halves and retry
before keeping the blocked verdict.


### §6.235 Critic model refusal → heuristics-only verify (#51199, 2026-09-01)

**Problem:** ``health-check-api.md`` verify on #51199 called YandexGPT critic;
every batch returned the safety refusal «Я не могу обсуждать эту тему» (HTTP
200, non-JSON prose). §6.220 retries exhausted and the file stayed
``critic_execution_failed`` even though heuristics were clean.

**Decision:**

1. Detect common refusal markers before JSON parse in ``_fetch_critic_response``.
2. Return ``critic_model_refusal`` (warning, non-blocking) instead of
   ``critic_execution_failed``.
3. ``run_critic_loop`` skips critic apply/verify and records a finalize warning;
   ``HeuristicsStep`` remains the sole gate for that file.

**Tests:** ``test_run_critic_model_refusal_falls_back_to_heuristics_only``,
``test_is_model_refusal_text_detects_yandexgpt_decline``.


### §6.236 Href-parity accepts RU translit via fragment_repair remap (#51761, 2026-09-01)

**Problem:** Translation PR #51761 / ``client_certificate_authorization.md``
stayed 🔴 on ``href_parity`` when RU kept the legacy translit fragment
``#vklyuchenie-rezhima-…`` while tip EN correctly linked
``#enabling-the-node-authentication-and-authorization-mode``. §6.227/228
fragment repair already knew the mapping; href-parity required an exact
``en_baseline_text`` slot match and failed without it (or when baseline still
had translit).

**Decision:** In the §6.174 declared-fragment pairing branch, accept a
missing/extra href pair when the EN fragment is declared on the target page and
``_remap_fragment_via_ru_en_pages`` maps the RU fragment to that EN slug (same
path, same link position). Baseline slot match remains a fast path.

**Tests:** ``test_pr_51761_href_parity_accepts_ru_translit_via_fragment_remap``.


### §6.237 Href-parity grandfather must use merge-base EN baseline on verify (#51761, 2026-09-01)

**Problem:** §6.236 remap never ran on ``doc_verify`` for
``client_certificate_authorization.md``: ``HeuristicsStep`` passed
``en_baseline_text=existing_target_text`` (tip EN with the declared EN slug).
Grandfather subtracted that slug from ``extra`` while tip RU path overlay
(``concepts/…`` vs source-base ``deployment-options/manual/…``) left a new
``missing`` translit href — remap requires both sides, so only «missing in EN»
remained.

**Decision:**

1. ``FileRunState.base_target_text`` on ``critic_only`` verify is
   ``content.en_base_text`` (merge-base EN), not ``ru_base_text``.
2. ``HeuristicsStep`` uses ``base_target_text or existing_target_text`` for
   ``en_baseline_text``.
3. After grandfather, rebuild position-aligned ``extra`` from ``tgt_ordered``
   when ``missing`` remains but ``extra`` was stripped (belt-and-suspenders).

**Tests:** ``test_pr_51761_href_parity_survives_tip_en_baseline_grandfather``.


### §6.238 Human-readable critic failure messages (#51199 / #51761, 2026-09-01)

**Problem:** QA reports showed ``(critic execution failed) Critic execution failed:
Invalid JSON in LLM response: Expecting value: line 1 column 1 (char 0)`` —
reviewers could not tell refusal vs empty JSON vs parse error or what to do.
§6.235 fixed refusal routing but old reports and ``critic_execution_failed`` still
used raw English exception text in ``builder._format_critic_item``.

**Decision:**

1. ``format_critic_reviewer_detail`` in ``heuristic_messages`` — bilingual RU
   problem + suggestion for ``critic_model_refusal`` and ``critic_execution_failed``.
2. ``_fallback_critic_response`` stores a safe ``raw_preview`` (≤200 chars, no prompts).
3. ``builder._format_critic_item`` uses the humanizer for those categories.
4. ``critic_model_refusal`` finalize warning is ``warnings``, not blocking.

**Tests:** ``test_format_critic_execution_failed_invalid_json``,
``test_format_critic_model_refusal``, ``test_humanize_critic_model_refusal_finalize_warning``.



### §6.239 Protect-atom publish boundary and exact ASCII fragments (#51797, 2026-09-01)

**Problem:** translation PR #51797 lost two Markdown link wrappers and published
four URL protect markers as percent-encoded ``%E2%9F%A6U…%E2%9F%A7``. The
pipeline recognized literal markers during validation, while Markdown parsing
could encode them. Separately, §6.225 remapped the ASCII RU transliteration
``#vklyuchenie-…`` to an EN-only auto-slug, conflicting with §6.174 and the
user-selected exact internal-link contract.

**Decision:**

1. Canonicalize encoded protect markers before placeholder multiset/role repair.
   Reinsert remains source-owned and no literal or encoded marker may publish.
2. Internal ASCII fragments, including RU legacy transliteration and explicit
   ids such as ``#sid``, remain byte-identical. They are never remapped.
3. When an exact ASCII fragment is missing, the final-tree workflow pairs RU/EN
   headings deterministically, adds ``{#exact-id}`` to the EN target, and adds
   that target page to the touched candidate overlay. Ambiguous pairing fails
   closed and the existing §6.226 ``en_link_target`` gate blocks publication.
4. Only fragments containing a Unicode Cyrillic character use deterministic
   RU/EN heading remapping. This narrows and supersedes the ASCII portion of
   §6.225 while preserving §6.174, §6.226, and §6.237 baseline semantics.
5. Whole Markdown wrappers are source-owned link slots. Finalization pairs RU
   current links with EN baseline links by ordinal, keeps the trusted EN label,
   and bounds restoration to the translated segment with the same
   `extract_segments` list ordinal and `SegmentKind` as the EN-baseline segment
   that owns the slot. Exactly one case-sensitive label occurrence inside that
   segment is required. Matches elsewhere are ignored; zero/two matches or
   ordinal/kind drift produce `missing_link_wrapper`. It never copies a RU
   label or guesses an ambiguous occurrence (SPEC-007).
6. The final-tree gate checks RU/EN link parity and marker exhaustion in
   addition to target existence. Thus ambiguous/deleted/reordered/extra
   wrappers and literal or encoded protect markers block before commit/push,
   including deterministic pair early returns.

**Tests:** ``test_pr_51797_percent_encoded_url_atoms_are_canonicalized_before_repair``,
``test_pr_51797_ascii_translit_is_declared_not_remapped``,
``test_pr_51797_candidate_overlay_adds_node_authorization_target``,
``test_pr_51797_cyrillic_fragment_is_the_only_remap_exception``,
``test_pr_51797_real_client_wrapper_full``,
``test_pr_51797_real_monitoring_wrapper_full``,
``test_pr_51797_final_missing_wrapper_blocks``,
``test_pr_51797_final_reordered_extra_and_marker_block``, and the
``test_pr_51797_linkslot_bounded_span_{zero,one,two}_exact_match*`` matrix.


### §6.240 Translation-PR verify stays inside source-PR EN scope (#40385 / #52055, 2026-09-03)

**Problem:** On translation PR ``ydbdoc-review/pr-*``, ``doc_verify`` built pairs
from the tip diff vs ``main``. Tip-ambient EN pages that drifted into the branch
were verified against current RU, produced unrelated 🔴 findings, and critic
pushed further ambient rewrites.
(stale ``compare-configs``, ``auth_config``, ``tracing/setup``, …) were verified
against current RU, produced unrelated 🔴 findings, and critic pushed further
ambient rewrites. Manual tip surgery then looked like “the translation failed”
even when the source-PR twins were fine.

**Decision:**

1. ``filter_translation_pr_verify_scope`` intersects the tip EN diff with the
   source-PR expected EN set.
2. ``verify_en_paths`` for late link gates excludes the same ambient set.
3. After critic apply, tip-ambient EN outside source scope is restored from
   ``merge_base_with`` and included in the inline fixup commit.
source-PR expected EN set (``expected_scope_pairs`` / ``scope_plan`` nav).
2. ``verify_en_paths`` for late link gates excludes the same ambient set.
3. After critic apply, tip-ambient EN outside source scope is restored from
   ``merge_base_with`` and included in the inline fixup commit so the
   translation PR stays scoped without operator strip.

Ambient RU/EN drift on ``main`` is out of scope for a translation PR; it needs
a separate docs sync, not critic fixes on the auto-translate branch.

**Tests:** ``test_filter_drops_tip_ambient_outside_source_pr_scope``,
``test_restore_out_of_scope_en_from_base``.


### §6.241 Exact-ASCII declare owns legacy translit headings (#40385 / R-GL-1, 2026-09-03)

**Problem:** ``_declare_exact_ascii_fragment_targets_after_apply`` skipped
``#vklyuchenie-…`` targets because ``_page_declares_fragment`` /
``_heading_declares_frag`` accepted only explicit ``{#id}`` and Diplodoc
auto-slug, not ``_legacy_transliterated_slug``. ``add_explicit_ascii_fragment_anchor``
already matched legacy, so owner discovery and add semantics diverged and
``en_link_target`` blocked publish after a clean translate.

**Decision:** Bare headings also declare their
``_legacy_transliterated_slug`` (same three-way ownership as
``add_explicit_ascii_fragment_anchor``). ASCII→EN-slug remapping stays
forbidden (§6.239 п.4). Ambiguous owners still fail closed.

**Tests:** ``test_page_declares_fragment_accepts_legacy_translit_slug``,
``test_pr_40385_legacy_translit_declare_writes_exact_ascii_and_clears_gate``.


### §6.242 Merged PR tip paths vs redirect tombstones (#40385 / R-GL-2, 2026-09-03)

**Problem:** GitHub ``doc_translate`` for a merged source PR checks out the
**merge commit**. Scope queued
``deployment-options/manual/node-authorization.md`` (live at merge). The
translation branch is built from current ``main``, where that path is only a
``redirects.yaml`` ``from`` → ``concepts/…``. Orphan gate uses tip TOC and
blocked publish. §6.224 tombstone skip did not fire because
``redirect_source_en`` preferred merge-commit ``redirects.yaml`` (no tombstone
yet) over tip.

**Decision:**

1. Tombstone skip / orphan exemption load ``redirects.yaml`` from
   ``merge_base_with`` (upstream tip), never from ``ru_content_ref`` merge SHA.
2. ``plan_translation_scope`` prefers tip redirects via ``read_en_base``;
   exact-ASCII fragment owners follow tip ``from`` → ``to``; synthetic
   tombstone deps retarget to the live twin (source-diff tombstones stay for
   skip/completeness).
3. ``make_repo_scope_readers`` ``read_ru`` falls back to tip when the merge
   tree lacks a tip-live path.
4. §6.241 legacy declare and §6.224 skip semantics unchanged for live paths.

**Tests:** ``test_r_gl_2_merge_era_manual_in_scope_tip_tombstone_skips_en_write``,
``test_r_gl_2_tip_redirect_retargets_fragment_owner_to_concepts``,
``test_tip_tombstone_skip_uses_tip_redirects_not_merge_era``,
``test_follow_redirect_repo_md_path_maps_manual_to_concepts``.



[← Memory Bank index](../../MEMORY_BANK.md)
