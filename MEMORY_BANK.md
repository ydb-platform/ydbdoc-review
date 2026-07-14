# Memory Bank — ydbdoc-review v2 (doc-translate-ng)

> Living, opinionated document. Treat it as authoritative for design intent.

**Last updated:** 2026-07-14  
**Current focus:** §22 unified navigation scope (Phase J complete on `main`, commit `d68812f`).

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
| Navigation scope | [09-navigation-scope](docs/memory-bank/09-navigation-scope.md) | 22 | TOC planner (authoritative for nav) |

## Recent changes

| When | What |
|------|------|
| 2026-07-14 | **§22 Phase J** — `scope_planner.py`; translate + verify share `TranslationScopePlan`; removed supplement modules (`d68812f` on `main`) |
| 2026-07-13 | §6.90 include closure after toc-href pass (#46393) |
| 2026-07-13 | §6.89 toc-href page supplementation (#46386) |
| 2026-07-12 | §6.85–§6.86 absent-EN toc mirror + indented `href` parse (#46349, #46346) |
| 2026-07-11 | §6.84 child toc via `include.path` (#46338) |

Older §6.x entries remain in [03-design-decisions](docs/memory-bank/03-design-decisions.md).

## Deploy status (navigation redesign)

| Artifact | State |
|----------|--------|
| `main` | §22 planner (`d68812f`) |
| Tags `v0.1.0` / `v0.2.0` | **Not moved yet** — still pre-§22 supplement chain |
| ydb CI | Uses action ref from tag; needs deliberate tag bump to pick up §22 |
| Validation | [#45181](https://github.com/ydb-platform/ydb/pull/45181) translation is green on old chain — **do not re-run**; test §22 on another PR after tag move |

## For AI assistants

1. Start with [01-overview](docs/memory-bank/01-overview.md) and [05-roadmap](docs/memory-bank/05-roadmap.md).
2. Open the part that matches your task (table above).
3. **Navigation / TOC work:** read [09-navigation-scope](docs/memory-bank/09-navigation-scope.md) §22 first. It supersedes §6.71–§6.90; historical rationale stays in §6.

Cross-reference cheat sheet: `§6.*` → 03-design-decisions · `§13.*` → 06-llm-config · `§15–17` → 07-pipeline · `§22` → 09-navigation-scope.

---

**End of Memory Bank index.**
