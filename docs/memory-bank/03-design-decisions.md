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

> **Status (2026-06):** wired in `github/workflow.py` via
> `pipeline/navigation_merge.py` (`run_navigation_merges`) after markdown
> translation. `build_navigation_pairs` detects changed RU `toc*.yaml` /
> redirect YAML; `completeness_gaps` (§6.32) blocks merge if any source PR
> mirror is missing from the commit.

Tests: `tests/unit/test_navigation_toc.py`, `test_navigation_redirects.py`,
`test_navigation_paths.py`, `test_validation_heuristics.py`.

**Inline TOC format (§6.33):** ydb `toc*.yaml` uses one-line items
`- { name: …, href: …, when: … }`. `parse_toc_items` must handle both this
and block `- name:` / `href:` layout. Empty merge (parser miss) is flagged
`empty_toc` + `scope_not_applied` → navigation verdict **blocked** → report 🔴.

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
`validate_toc_merge` adds `empty_toc`; `scope_not_applied`, `missing_href`,
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

**Decision:** ``_read_navigation_baselines()`` — RU at merge-base; EN at
merge-base with **fallback to** ``merge_base_with`` (upstream ``main``).
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
   - RU changed (with or without EN changed) → `translate_to_en` from RU when RU
     text exists (default YDB path).
   - EN changed, RU unchanged → `translate_to_ru` from EN.
   - Both changed, RU missing → `translate_to_ru` from EN.
3. **No LLM analyze for action selection** in CI (`plan_pairs`, `use_analyze_llm=False`).
   `critic_only` remains only for **`doc_verify`** (`enable_translate=False`).
4. Pair with **`gate_round_trip`** (§6.29) blocks merge when render does not preserve
   segment parity.

**Tests:** `tests/unit/test_pipeline_analyze.py` (both-changed → RU→EN);
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

Non-fork case (translation PR on upstream) keeps the current direct-push path.
No critic fixes (`touched` empty) → no fixup PR, only the QA report comment.

> **Superseded (§6.64):** direct-push to ``ctx.head_ref`` for same-repo PRs is
> removed — all critic fixes use the fixup branch/PR path above.

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

**Decision:** before the fixup push, drop the stale remote ref via
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

### 6.64. `doc_verify` critic fixes — always separate fixup branch/PR

**Problem:** §6.50 added a fork-only fixup path, but same-repo ``doc_verify`` still
pushed critic commits directly onto the verified PR head — including unmerged
author branches and translation branches ``ydbdoc-review/pr-N``. Authors object to
bot commits landing on their feature branches without an explicit review PR.

**Decision:** **never** push critic fixes onto ``ctx.head_ref``. Every ``doc_verify``
run with applied fixes:

1. Resets ``ydbdoc-review/verify-{source_pr or pr_number}`` off
   ``translation_branch_base(ctx)`` (translation head for same-repo open PRs;
   ``base_ref`` for fork/merged — same helper as ``doc_translate``).
2. Commits critic fixes and pushes that branch to upstream.
3. Opens a fixup PR via ``gh.create_pull``:
   - **translation PR** on upstream → base ``ctx.head_ref`` (merge fixes into the
     translation branch, not the author's feature branch).
   - **all other PRs** → base ``ctx.base_ref``.
4. Posts QA report on the verified PR + link comment to the fixup PR.

§6.52 stale-branch ``delete_branch`` before push applies to **all** fixup runs, not
only fork heads. Direct-push via ``verify_push_remote_url`` → ``ctx.head_ref`` is
removed.

**Implementation:** ``run_doc_verify`` in ``workflow.py``,
``verify_fixup_pr_base`` in ``pr.py``, updated ``build_verify_fixup_*`` messages in
``reporting/builder.py``.

**Tests:** ``test_run_doc_verify_translation_pr_opens_fixup_pr``,
``test_run_doc_verify_same_repo_author_pr_opens_fixup_pr``,
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
   not cascade).

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

**Tests:** ``test_merge_supplement_only_adds_translated_href_not_full_ru_gap``.

---
