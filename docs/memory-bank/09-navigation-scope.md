# Memory Bank — Navigation scope redesign (TOC)

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

---

## 22. Unified navigation scope (TOC redesign)

> **Status:** Phase J complete (2026-07-14, `d68812f`).  
> **Supersedes:** §6.71–§6.90 supplement chain (historical detail in **03-design-decisions** §6).

### 22.1. Problem statement

TOC handling grew as reactive fixes (#43365, #44103, #46338, #46349, #46386,
#46393). Logic was split across three supplement modules plus overlapping axes in
`navigation_merge.py` (`extra_toc_hrefs`, ordering between passes). That caused
translate/verify scope drift and ordering bugs.

**Removed modules (Phase J):** `navigation_supplement.py`, `toc_href_supplement.py`,
`include_supplement.py`.

### 22.2. Target model

For each **source PR**:

1. **Discover related navigation** — every `toc_p.yaml` / `toc_i.yaml` tied to the
   PR (direct diff, ancestor sidebars of changed pages, child sidebars via
   `include.path`).
2. **Build an in-memory page tree** — for each sidebar: ordered `href` pages +
   nested sidebars (`include.path` → child toc).
3. **Plan translation scope** before any LLM call:
   - **From diff:** RU paths touched in the source PR → translate from PR head.
   - **From main:** only paths required by **diff-scoped** toc rules + locale
     ``{% include %}`` closure — **not** whole absent-EN sibling sections (§6.104).
4. **Close dependencies:** locale `{% include %}` for every queued `.md`.
5. **Merge / verify navigation** from the same plan object — no second-guessing scope.

### 22.3. Core types (`navigation/scope_planner.py`)

```python
@dataclass(frozen=True)
class TranslationScopePlan:
    doc_ru_paths: frozenset[str]      # all RU .md to translate
    doc_from_diff: frozenset[str]     # provenance: source PR diff
    doc_from_main: frozenset[str]     # provenance: pulled from main tree
    nav_ru_paths: frozenset[str]      # all RU toc/redirect yaml to merge
    nav_from_diff: frozenset[str]
    nav_from_main: frozenset[str]
```

**Planner entrypoint:** `plan_translation_scope(changes, read_ru=…, read_en_base=…, read_ru_base=…)`.

`read_ru_base` supplies RU toc/page text at merge-base so step 3 can diff **new**
``href`` entries when a toc yaml is in the PR diff (§22.10).

Injected readers keep the planner pure (unit tests use fixture files, workflow
uses `read_text` / `read_text_at_ref` via `make_repo_scope_readers()`).

**Workflow helpers:** `doc_pairs_from_plan`, `navigation_pairs_from_plan`,
`merge_navigation_pair_lists`, `synthetic_changes_from_plan`,
`planned_toc_extras_for_pair` (merge/verify extras per sidebar).

### 22.4. Discovery algorithm (v1)

| Step | Action |
|------|--------|
| 1 | Seed `diff_ru_md` / `diff_ru_nav` from PR file list |
| 2 | `_discover_ru_tocs` — ancestor tocs + BFS on `include.path` **only into child sidebars whose directory contains a diff file or that are in diff-nav** (§6.104) |
| 3 | Per discovered toc (§22.5): **toc in PR diff** → **new** ``href`` since merge-base only; **partial EN sidebar** → missing EN mirrors for **diff pages listed in that toc**. No queue-all-hrefs for absent EN sibling sections. |
| 4 | BFS locale `{% include %}` on all `doc_ru` |
| 5 | `_nav_needed` — queue toc merge if: in diff, EN toc absent, or EN missing href for a diff page |

Merge phase (unchanged location: `navigation_merge.py`):

- Label translation via LLM
- `supplement_only` flag from `nav_from_main` provenance
- Scoped gap-fill (§6.72) via `_resolve_toc_merge_scope`; absent-EN **full mirror**
  only at **merge** time for nav yaml already in plan (not scope expansion §6.104)
- `planned_toc_extras_for_pair` replaces `extra_toc_hrefs_from_md_targets` axis

### 22.5. Operational rules (authoritative)

| EN `main` state | Pages | Navigation merge |
|-----------------|-------|------------------|
| Section entirely absent (no EN toc / pages) | **Not** auto-queued via sibling toc BFS (§6.104). Only diff pages + new toc hrefs in diff + include closure. | `_nav_needed` may still queue toc yaml when EN absent **and** a diff page is listed there |
| Partial EN sidebar | Translate only missing EN mirrors for **diff** pages listed in toc | Supplement missing href/include only (§6.72) |
| PR edits toc yaml directly | Translate diff pages + toc labels in scope | Scoped merge from RU base→PR diff (§6.82) |

### 22.6. Real PR golden fixtures

Fetched by `scripts/fetch_nav_fixtures.py` into `tests/fixtures/nav_cases/`:

| Case | Source PR | What it exercises |
|------|-----------|-------------------|
| `case_45181` | [#45181](https://github.com/ydb-platform/ydb/pull/45181) | Diff = topic + diagnostics only; **sqs-api** entire subtree via `reference/toc_p.yaml` → `include.path`; includes |
| `case_44820` | [#44820](https://github.com/ydb-platform/ydb/pull/44820) | SQS pages + `reference/toc_p.yaml` **in diff** |
| `case_43530` | [#43530](https://github.com/ydb-platform/ydb/pull/43530) | OTel observability — **explicit toc edits** in diff (#44103) |
| `case_44457` | [#44457](https://github.com/ydb-platform/ydb/pull/44457) | Partial EN sidebar — diff + new toc href only; **not** all missing pages in ancestor menus |
| `case_43997` | [#43997](https://github.com/ydb-platform/ydb/pull/43997) | Java SDK recipe snippets — **exactly 20** diff md paths; no json-search / streaming-query / spring spill (§6.104) |

Tests: `tests/unit/test_nav_scope_planner.py`.

Refresh fixtures after upstream doc moves:

```bash
python scripts/fetch_nav_fixtures.py --all
```

### 22.7. Implementation checklist (Phase J)

| Step | Deliverable | Status |
|------|-------------|--------|
| J.1 | Design doc + roadmap | ✅ |
| J.2 | `scope_planner.py` | ✅ |
| J.3 | Real PR fixtures + fetch script | ✅ |
| J.4 | Golden tests (#45181, #44820, #43530) | ✅ |
| J.5 | `doc_translate` calls planner once; single MD pass | ✅ |
| J.6 | Merge reads plan; drop `extra_toc_hrefs` axis | ✅ |
| J.7 | `doc_verify` uses same planner; delete legacy modules | ✅ |

**Not started:** redirect yaml in planner (same pattern as toc); report provenance tags in UI.

### 22.8. Rollout and validation (2026-07-14)

| Item | Decision |
|------|----------|
| Code on `main` | `d68812f` (Phase J planner) + follow-ups through `203956a` |
| Tag `v0.1.0` | **`7685056`** (2026-07-15) — §6.104 scope BFS + no cross-section mirror; §6.105 Cyrillic `#fragment` remap |
| Tag `v0.2.0` | Unchanged — Reactor/Nirvana schedulers only |
| [#45181](https://github.com/ydb-platform/ydb/pull/45181) | Translation PR is **green on old chain** — do **not** re-run for regression |
| §22 validation | Step-by-step: [#44457](https://github.com/ydb-platform/ydb/pull/44457) (CI re-run), [#43010](https://github.com/ydb-platform/ydb/pull/43010) (local Eliza), [#43997](https://github.com/ydb-platform/ydb/pull/43997) |

**First §22 rollout (2026-07-14):** three auto-translate PRs were created before the
step-3 fix landed; see §22.10. Re-run source PRs one at a time after `v0.1.0` moves.

**Re-trigger `doc_translate` (when needed):**

1. Delete branch `ydbdoc-review/pr-{N}` in `ydb-platform/ydb` (closes old translation PR).
2. On the **source PR**, remove and re-add label `doc_translate` (not on the translation PR).

**Tag bump loop (when ready):**

```bash
git tag -f v0.1.0 HEAD && git push -f origin v0.1.0
# optional: v0.2.0 for Reactor/Nirvana schedulers
```

### 22.9. Relationship to §6.84–§6.90

| Old § | New home |
|-------|----------|
| §6.84 child toc via `include.path` | `_discover_ru_tocs` BFS |
| §6.85 absent EN full mirror | `_nav_needed` + merge scope |
| §6.89 toc href → md pages | step 3 in §22.4 |
| §6.90 include after toc-href | step 4 in §22.4 (same pass, no ordering bug) |

### 22.10. First §22 rollout incident (2026-07-14)

Three source PRs were translated the same morning with label `doc_translate` while
`v0.1.0` still pointed at pre-fix commits. Symptoms split into **scope** vs **runtime**
vs **build-docs**.

| Translation PR | Source PR | Files (bad run) | Primary failure |
|----------------|-----------|-----------------|-----------------|
| [#46451](https://github.com/ydb-platform/ydb/pull/46451) | [#44457](https://github.com/ydb-platform/ydb/pull/44457) | 35 | `build-docs`: MD037 in `glossary.md` (`** [` bold links) |
| [#46454](https://github.com/ydb-platform/ydb/pull/46454) | [#43997](https://github.com/ydb-platform/ydb/pull/43997) | 49 | `build-docs`: MD051 RU fragment `#векторный-поиск` in `vector-search.md` |
| [#46461](https://github.com/ydb-platform/ydb/pull/46461) | [#43010](https://github.com/ydb-platform/ydb/pull/43010) | 51 | `build-docs`: YFM003 unreachable links + MD037 glossary |

**Scope (35/49/51 files):** step 3 treated every discovered ancestor toc as “queue all
missing EN hrefs”, pulling postgresql, public-materials, secondary_indexes, etc.
**Fix:** `caff954` — per-toc rules in §22.4 step 3 + `read_ru_base` for new-href diff.
Golden: `case_44457` expects ~3 md + 1 toc, not 35.

**Runtime (re-runs):** `NameError: build_segment_source_excerpts` in
`ReportArtifactsStep` after `c2d713f`. **Fix:** `c32479a` import in `harness/steps.py`.

**build-docs (glossary):** translator emits `** [term](url)**` instead of
`**[term](url)**`. **Fix:** `55ba789` — `fix_no_space_in_emphasis()` in
`validation/markdown_layout.py` (postprocess).

**Operational rule:** debug one source PR at a time; do not batch-re-run all three until
[#44457](https://github.com/ydb-platform/ydb/pull/44457) is green end-to-end.

### 22.11. §22 validation progress (2026-07-15)

| Source PR | Mode | Scope (expected) | Status |
|-----------|------|------------------|--------|
| [#44457](https://github.com/ydb-platform/ydb/pull/44457) | ydb CI `doc_translate` | ~3 md + nav (`case_44457`) | Validated @ `203956a`+ |
| [#43010](https://github.com/ydb-platform/ydb/pull/43010) | Local Eliza dry-run | 13 doc + 8 nav | Done locally |
| [#43997](https://github.com/ydb-platform/ydb/pull/43997) | Local `job` + Eliza (re-run) | **20 md** + nav for touched ydb-sdk/reference (`case_43997`) | **Re-run** after `v0.1.0` @ §6.104–§6.105 — delete `ydbdoc-review/pr-43997`, old [#46577](https://github.com/ydb-platform/ydb/pull/46577) had 36 files + MD051 |

**Wikipedia manual fix (#44457):** DML → `https://en.wikipedia.org/wiki/Data_manipulation_language`;
DDL → `https://en.wikipedia.org/wiki/Data_definition_language`.

### 22.12. Cross-section scope overrun (#43997 → #46577, 2026-07-15)

**Symptom:** source [#43997](https://github.com/ydb-platform/ydb/pull/43997) (20 RU files) → translation [#46577](https://github.com/ydb-platform/ydb/pull/46577) (36 files): json-search, streaming-query, spring, sql-translation pulled via lateral BFS + absent-EN full mirror on sibling tocs under `recipes/toc_p.yaml` and `reference/toc_p.yaml`.

**Fix:** §6.104 — `_toc_dir_contains_diff` gates BFS; absent-EN mirror block removed from `_pages_from_discovered_toc`. Golden: `case_43997` (exact 20), `case_45181` no longer pulls sqs-api (sqs only when in diff, e.g. #44820).

**build-docs:** MD051 on `vector-search.md` `[Vector search](#векторный-поиск)` — §6.105 heading anchor map + link_locale remap.

---

[← Memory Bank index](../../MEMORY_BANK.md)
