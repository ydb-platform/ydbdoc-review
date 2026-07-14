# Memory Bank — Navigation scope redesign (TOC)

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

---

## 22. Unified navigation scope (TOC redesign)

> **Status:** Phase 2 complete — translate + verify share `TranslationScopePlan` (J.7).  
> **Replaces incrementally:** §6.71–§6.90 patchwork (`navigation_supplement`,  
> `toc_href_supplement`, dual supplementation passes in `workflow.py`).

### 22.1. Problem statement

Current TOC handling grew as reactive fixes (#43365, #44103, #46338, #46349,
#46386, #46393). Logic is split across:

- `navigation_supplement.py` — MD → parent toc
- `toc_href_supplement.py` — toc → MD pages
- `include_supplement.py` — MD → locale `{% include %}`
- `navigation_merge.py` — scope + merge + three overlapping axes (`diff scope`,
  `extra_toc_hrefs`, `supplement_only`, absent-EN mirror)

Ordering bugs are inevitable; `doc_translate` and `doc_verify` disagree on scope.

### 22.2. Target model (user requirement)

For each **source PR**:

1. **Discover related navigation** — every `toc_p.yaml` / `toc_i.yaml` tied to the
   PR (direct diff, ancestor sidebars of changed pages, child sidebars via
   `include.path`).
2. **Build an in-memory page tree** — for each sidebar: ordered `href` pages +
   nested sidebars (`include.path` → child toc).
3. **Plan translation scope** before any LLM call:
   - **From diff:** RU paths touched in the source PR → translate from PR head.
   - **From main:** RU paths required by the tree but absent in EN at merge-base
     → read RU from `main`/HEAD and translate (full section mirror).
4. **Close dependencies:** locale `{% include %}` for every queued `.md`.
5. **Merge navigation** once, from the same plan (no second-guessing scope).

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

**Planner entrypoint:** `plan_translation_scope(changes, read_ru=…, read_en_base=…)`.

Injected readers keep the planner pure (unit tests use fixture files, workflow
uses `read_text` / `read_text_at_ref`).

### 22.4. Discovery algorithm (v1)

| Step | Action |
|------|--------|
| 1 | Seed `diff_ru_md` / `diff_ru_nav` from PR file list |
| 2 | `_discover_ru_tocs` — union of diff nav + ancestor tocs of each diff md + BFS on `include.path` |
| 3 | For each discovered toc: every `href: *.md` where RU exists and EN absent (or in diff) → `doc_ru` |
| 4 | BFS locale `{% include %}` on all `doc_ru` |
| 5 | `_nav_needed` — queue toc merge if: in diff, EN toc absent, or EN missing href for a diff page |

**Not in v1 planner (merge phase, step 2):**

- Label translation strategy (unchanged — still in `navigation_merge.py`)
- Redirect yaml (parallel module; same planner pattern later)
- `extra_toc_hrefs` as separate axis — folded into step 3–5

### 22.5. Operational rules (authoritative)

| EN `main` state | Pages | Navigation merge |
|-----------------|-------|------------------|
| Section entirely absent (no EN toc / pages) | Translate **all** RU `href` + includes from tree | Full RU mirror (§6.85) |
| Partial EN sidebar | Translate only missing EN mirrors | Supplement missing href/include only (§6.72) |
| PR edits toc yaml directly | Translate diff pages + toc labels in scope | Scoped merge from RU base→PR diff (§6.82) |

### 22.6. Real PR golden fixtures

Fetched by `scripts/fetch_nav_fixtures.py` into `tests/fixtures/nav_cases/`:

| Case | Source PR | What it exercises |
|------|-----------|-------------------|
| `case_45181` | [#45181](https://github.com/ydb-platform/ydb/pull/45181) | Diff = topic + diagnostics only; **sqs-api** entire subtree via `reference/toc_p.yaml` → `include.path`; includes (#46393) |
| `case_44820` | [#44820](https://github.com/ydb-platform/ydb/pull/44820) | SQS pages + `reference/toc_p.yaml` **in diff** |
| `case_43530` | [#43530](https://github.com/ydb-platform/ydb/pull/43530) | OTel observability — **explicit toc edits** in diff (#44103) |

Tests: `tests/unit/test_nav_scope_planner.py`.

Refresh fixtures after upstream doc moves:

```bash
python scripts/fetch_nav_fixtures.py --all
```

### 22.7. Implementation roadmap

| Step | Deliverable | Status |
|------|-------------|--------|
| **A** | `scope_planner.py` + golden tests on real PR fixtures | ✅ |
| **B** | `workflow.py` calls planner once; single MD pass (J.5) | ✅ |
| **C** | `navigation_merge.py` consumes plan; drop `extra_toc_hrefs` axis (J.6) | ✅ |
| **D** | `doc_verify` uses same planner; delete legacy modules (J.7) | ✅ |

### 22.8. Suggested additions to user idea

1. **Provenance tags** (`doc_from_diff` / `doc_from_main`) — audit trail in reports
   and completeness checks; explains why a file outside diff was translated.
2. **Single plan object** passed through translate → merge → verify — eliminates
   translate/verify scope drift.
3. **Fixture-first development** — every regression (#46386, #46393) becomes a
   `nav_cases/` snapshot + planner assertion before touching workflow glue.
4. **EN baseline reader** — explicit `read_en_base` simulates merge-base; fork PR
   fallback (§6.44) stays in workflow adapter, not planner core.
5. **Redirect yaml** — same planner pattern as toc (Phase 22 step F, not started).
6. **No translate before plan** — workflow must not call LLM until
   `TranslationScopePlan` is complete (fixes ordering class permanently).

### 22.9. Relationship to §6.84–§6.90

| Old § | New home |
|-------|----------|
| §6.84 child toc via `include.path` | `_discover_ru_tocs` BFS |
| §6.85 absent EN full mirror | `_nav_needed` + all hrefs when EN absent |
| §6.89 toc href → md pages | step 3 in §22.4 |
| §6.90 include after toc-href | step 4 in §22.4 (same pass, no ordering bug) |

Old modules remain until step B–E land; planner tests define the contract they
must match.

---

**End of §22.**
