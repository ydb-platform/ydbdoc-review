# Memory Bank — ydbdoc-review v2 (doc-translate-ng)

> Living, opinionated document. Treat it as authoritative for design intent.  
> Last updated: §6.36 TOC indent from EN-main; §6.35 doc_verify nav; §6.34 link_locale;
> §6.31 verify RU ref;
> §6.30 full re-translate;
> §6.29 unified QA;
> §6.28 finalize order;
> §6.25–§6.27 critic verdict / report checkout; §16.7 CI tokens.

The Memory Bank is **deliberately verbose** — written so any developer or AI
assistant can reconstruct full project context. It no longer lives in one file;
use the parts below.

## Contents

| Part | Sections | Topics |
|------|----------|--------|
| [01 — Overview](docs/memory-bank/01-overview.md) | §0–§3 | Pointers, goals, v1 lessons, architecture |
| [02 — Codebase](docs/memory-bank/02-codebase.md) | §4–§5 | Package layout, AST / IR reference |
| [03 — Design decisions](docs/memory-bank/03-design-decisions.md) | §6 | Trade-offs, TOC scope, caching, … |
| [04 — Development](docs/memory-bank/04-development.md) | §7, §9–§11 | Tests, backlog, working agreements, env |
| [05 — Roadmap](docs/memory-bank/05-roadmap.md) | §8 | Phase A–I checklist (living) |
| [06 — LLM & config](docs/memory-bank/06-llm-config.md) | §12–§14, §18 | Yandex AI Studio, YAML config, glossary, prompts |
| [07 — Pipeline](docs/memory-bank/07-pipeline.md) | §15–§17 | Per-file flow, PR behavior, reports |
| [08 — Operations](docs/memory-bank/08-operations.md) | §19–§21 | Logging, cost tracking, terminology |

## Section index (quick lookup)

| § | File |
|---|------|
| 0–3 | [01-overview](docs/memory-bank/01-overview.md) |
| 4–5 | [02-codebase](docs/memory-bank/02-codebase.md) |
| 6 (incl. §6.36 TOC indent, §6.35 verify nav) | [03-design-decisions](docs/memory-bank/03-design-decisions.md) |
| 7, 9–11 | [04-development](docs/memory-bank/04-development.md) |
| 8 | [05-roadmap](docs/memory-bank/05-roadmap.md) | Phases A–I done; nav YAML workflow glue TBD |
| 12–14, 18 | [06-llm-config](docs/memory-bank/06-llm-config.md) |
| 15–17 (incl. §16.7 CI tokens) | [07-pipeline](docs/memory-bank/07-pipeline.md) |
| 19–21 | [08-operations](docs/memory-bank/08-operations.md) |

## For AI assistants

Start with **01-overview** and **05-roadmap**, then open the part matching your
task. Cross-references like `§6.12` are in **03-design-decisions**; `§13.3` is
in **06-llm-config**.

---

**End of Memory Bank index.**
