# Memory Bank — ydbdoc-review v2 (doc-translate-ng)

> Living, opinionated document. Treat it as authoritative for design intent.

**Last updated:** 2026-07-19  
**Current focus:** §6.120 — merged PR translate from merge commit (#47100 YFM010).

The Memory Bank is split into parts below. Section numbers (`§6.12`, `§22.3`, …) are
stable cross-references — use them when linking between files.

## Contents

| Part | File | § | Topics |
|------|------|---|--------|
| Overview & architecture | [01-overview](docs/memory-bank/01-overview.md) | 0–3 | Goals, v1 lessons, data flow |
| Codebase reference | [02-codebase](docs/memory-bank/02-codebase.md) | 4–5 | Package layout, AST / IR |
| Design decisions | [03-design-decisions](docs/memory-bank/03-design-decisions.md) | 6 | Trade-offs, historical fixes |
| Development guide | [04-development](docs/memory-bank/04-development.md) | 7, 9–11 | Tests, backlog, env, agreements |
| Roadmap | [05-roadmap](docs/memory-bank/05-roadmap.md) | 8 | Phases A–J checklist |
| LLM & config | [06-llm-config](docs/memory-bank/06-llm-config.md) | 12–14, 18 | Models, YAML config, prompts |
| Pipeline & reporting | [07-pipeline](docs/memory-bank/07-pipeline.md) | 15–17 | Per-file flow, PR workflow, reports |
| Operations | [08-operations](docs/memory-bank/08-operations.md) | 19–21 | Action runtime, cost, glossary |
| Navigation scope | [09-navigation-scope](docs/memory-bank/09-navigation-scope.md) | 22 | TOC planner + **§22.14 regression catalog** |

## Recent changes

| When | What |
|------|------|
| 2026-07-19 | **§6.120** — merged source PR: ``doc_translate`` RU from ``merge_commit_sha``; force exact ``{#T}`` hrefs RU→EN (#47100 YFM010) |
| 2026-07-19 | **§6.119** — `supplement_only` must not expand to all RU−EN missing hrefs (#46878) |
| 2026-07-19 | **§22.14** — TOC PR regression catalog: `test_toc_pr_regressions.py` covers validate/planner/merge/QA kinds from failing PRs |
| 2026-07-19 | **§6.118** — parse/validate keep `include_path` on href+include toc entries (#47100 false `scope_not_applied`) |
| 2026-07-19 | **§6.117** — blocking `orphan_toc_page` when translated EN `.md` is not reachable from EN toc graph |
| 2026-07-19 | **§6.116** — queue parent toc when it `include.path`s a needed child sidebar (#46569 pages translated but off EN nav tree) |
| 2026-07-17 | **§6.111–§6.115** — EN toc baseline on main; harness strip wiring; Table/YfmIf walkers; strip↔verify alignment (#39856) |
| 2026-07-15 | **§6.110** — `doc_verify` pick RU among head/merge/local (#46674); offline DDL/DML Wikipedia map |
| 2026-07-15 | **§6.108** — fix EN-only toc BFS for link strip (no RU toc pollution); strip all scoped EN md, not glossary-only (#46637) |
| 2026-07-15 | **§6.107** — glossary profile + Wikipedia Wikidata langlinks; glossary YFM003 variant A (strip unreachable internal links); re-run [#44457](https://github.com/ydb-platform/ydb/pull/44457) |
| 2026-07-15 | **§6.106** — `doc_verify` RU from merge commit + fence-body tie-break for merged source PR (#43997/#46609 false `fence_body_copy`) |
| 2026-07-15 | **§6.104–§6.105** — scope BFS gate + no cross-section absent-EN mirror (`case_43997`); Cyrillic `#fragment` remap via heading anchor map + link_locale validator |
| 2026-07-15 | **§6.103** — Eliza ordered model chains (translate/critic); env `YDBDOC_ELIZA_*_FALLBACKS` + YAML `llm.eliza` |
| 2026-07-15 | **§6.102** — drop redundant «автоисправления в этой ветке» comment on translation PR; QA report only |
| 2026-07-14 | **§6.101** — fix `format_heuristic_location` (`file_url` → `format_line_ref`); #46475 CI crash after translate OK |
| 2026-07-14 | **§6.96–§6.100** — report UX; Eliza 429 fallback; TLS split; CLI shutdown; pytest conftest isolates provider |
| 2026-07-14 | **`v0.1.0` tag moved** — includes §6.101 + Eliza/TLS hardening (after `203956a`) |
| 2026-07-14 | **§22 rollout** — re-run [#44457](https://github.com/ydb-platform/ydb/pull/44457); local debug [#43010](https://github.com/ydb-platform/ydb/pull/43010) via Eliza (`job --dry-run`) |
| 2026-07-14 | **§22 Phase J** — `scope_planner.py`; translate + verify share `TranslationScopePlan`; removed supplement modules (`d68812f` on `main`) |
| 2026-07-13 | §6.90 include closure after toc-href pass (#46393) |
| 2026-07-13 | §6.89 toc-href page supplementation (#46386) |
| 2026-07-12 | §6.85–§6.86 absent-EN toc mirror + indented `href` parse (#46349, #46346) |
| 2026-07-11 | §6.84 child toc via `include.path` (#46338) |

Older §6.x entries remain in [03-design-decisions](docs/memory-bank/03-design-decisions.md).

## Deploy status (navigation redesign)

| Artifact | State |
|----------|--------|
| `main` | §22 planner + §6.101–§6.106 (tagged `v0.1.0`) |
| Tag `v0.1.0` | **moved** on 2026-07-15 — §6.106 verify RU authority + §6.104–§6.105 |
| Tag `v0.2.0` | Unchanged — Reactor/Nirvana schedulers only |
| ydb CI `doc_translate` | **Yandex Cloud** (`YANDEX_CLOUD_*` secrets); default `YDBDOC_MODEL_PROVIDER=yandex_cloud` — **not** Eliza |
| Local `job` / Reactor | **Eliza** when `YDBDOC_MODEL_PROVIDER=eliza` + `ELIZA_OAUTH_TOKEN` (typically `~/.zshrc`) |
| Validation | [#46609](https://github.com/ydb-platform/ydb/pull/46609): re-run **`doc_verify`** after tag @ §6.106 (expect ~8 fewer false fence 🟡) |

## For AI assistants

1. Start with [01-overview](docs/memory-bank/01-overview.md) and [05-roadmap](docs/memory-bank/05-roadmap.md).
2. Open the part that matches your task (table above).
3. **Navigation / TOC work:** read [09-navigation-scope](docs/memory-bank/09-navigation-scope.md) §22 first. It supersedes §6.71–§6.90; historical rationale stays in §6.

Cross-reference cheat sheet: `§6.*` → 03-design-decisions · `§13.*` → 06-llm-config · `§15–17` → 07-pipeline · `§22` → 09-navigation-scope.

---

**End of Memory Bank index.**
