# Memory Bank — ydbdoc-review v2 (doc-translate-ng)

> Living, opinionated document. Treat it as authoritative for design intent.  
> Last updated: §22 unified navigation scope planner (Phase 1 tests); §6.90 include after toc-href (#46393); §6.89 toc href page supplementation (#46386); §6.86 indented block toc ``href`` parse (#46346); §6.85 mirror absent EN toc (#46349); §6.84 inline toc include + child toc supplementation (#46338); §6.83 EN toc target existence + rebuild_docs checkout (#45157/#46258); §6.82 restrict toc gap-fill (#46258); §6.81 trailing ``//`` fence comments + multi-comment pipeline tests (#44758); §6.80.2–§6.80.4 parser + narrow include supplement + source comment UX (#44880); §6.80 locale include dependency closure (#44880/#45056); §6.79 Cyrillic tab homoglyph whitelist (#45053); §6.78 English YFM anchors + hallucinated link repair (#45053); §6.77 translation PR verify scope (#45053); §6.76 skip doc_translate when both RU and EN changed (#44191); §6.75 translation PR inline critic fixes (#45042, `ed59666`); §6.74 validate_toc legacy href alias (#44942); §6.73 inline doc_verify in doc_translate (#44912); §6.72 supplement toc restrict gap fill (#44916); §6.71 parent toc supplement + prose angle placeholders (#44889); §6.70 doc_verify RU checkout fallback (#44872); §6.69 split doc_translate/doc_verify; §6.67 #44872 KV format align; §6.65 #44268 formula placeholder align; §6.64 fixup PR for author/fork verify; §6.63 #44117 nested indented toc parse/merge; §6.62 #44103 text fence QA + toc include merge; §6.61 #43860 doc_verify noise; §6.60 #43746 inline-code backtick render; §6.58 #40466 doc_verify validation (post-§6.57); §6.57 doc_verify false-positive filters round 2 (#40466); §6.56 doc_verify noise reduction (#40466); §6.55 cross-language placeholder alignment (#40466 columns.md); §6.54 mermaid Note/arrow + ⟦V⟧ drift filter (#41206); §6.53 critic regression guard + mermaid fence compare; §6.52 doc_verify fork fallback resets stale fixup branch before push; §6.51 doc_verify EN render base preserves EN fence bodies (#43399); §6.50 doc_verify fork fallback (#41451); §6.49 action Docker build + GHCR fallback; §6.48 translation report before source comment; §16.7 YDBOT_TOKEN CI split (#43126); §6.47 RU ``-rub`` asset paths; §6.46 YQL ``--`` fence comments; §6.45 prose Cyrillic cleanup; §6.44 fork nav EN baseline; §6.41 locale _includes; §6.39 fence comment Cyrillic; §6.38 cost reporting (₽/1K); §6.37 Wikipedia langlinks; §6.36 TOC indent;
> §6.35 doc_verify nav; §6.34 link_locale; §6.31 verify RU ref;
> §6.30 full re-translate (except §6.76 bilingual skip);
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
| [09 — Navigation scope](docs/memory-bank/09-navigation-scope.md) | §22 | Unified TOC tree + translation scope planner (redesign) |

## Section index (quick lookup)

| § | File |
|---|------|
| 0–3 | [01-overview](docs/memory-bank/01-overview.md) |
| 4–5 | [02-codebase](docs/memory-bank/02-codebase.md) |
| 6 (incl. §6.89 toc href page supplementation (#46386), §6.88 Eliza internal route + env-only OAuth, §6.86 indented block toc href (#46346), §6.85 mirror absent EN toc (#46349), §6.84 inline include + child toc supplement (#46338), §6.83 toc target existence, §6.82 restrict gap-fill, §6.75 translation PR inline critic fixes, §6.66 per-file + PR harness, §6.64 author/fork fixup PR, §6.63 #44117 nested toc indent, §6.62 #44103 text fence + toc include, §6.61 #43860 doc_verify noise, §6.60 #43746 inline-code render, §6.59 #43365 auto-translate, §6.57 doc_verify false-positive filters round 2, §6.56 doc_verify noise reduction, §6.55 cross-language placeholder alignment, §6.53 critic regression guard, §6.52 doc_verify fixup reset, §6.51 doc_verify EN render base, §6.50 doc_verify fork fallback, §6.49 action Docker/GHCR, §6.48 report comment order, §6.47 ``-rub`` assets, §6.46 YQL ``--`` comments, §6.45 prose Cyrillic, §6.44 fork nav EN baseline, §6.43–§6.41, §6.40–§6.39, §6.37–§6.36) | [03-design-decisions](docs/memory-bank/03-design-decisions.md) |
| 7, 9–11 | [04-development](docs/memory-bank/04-development.md) |
| 8 | [05-roadmap](docs/memory-bank/05-roadmap.md) | Phases A–I done; nav YAML workflow glue TBD |
| 12–14, 18 | [06-llm-config](docs/memory-bank/06-llm-config.md) |
| 15–17 (incl. §16.7 GITHUB_TOKEN + YDBOT_TOKEN, §17.0 report order) | [07-pipeline](docs/memory-bank/07-pipeline.md) |
| 19–21 (incl. §19.4 action-docker.sh / GHCR) | [08-operations](docs/memory-bank/08-operations.md) |
| 22 (nav scope redesign — planner + golden PR fixtures) | [09-navigation-scope](docs/memory-bank/09-navigation-scope.md) |

## For AI assistants

Start with **01-overview** and **05-roadmap**, then open the part matching your
task. Cross-references like `§6.12` are in **03-design-decisions**; `§13.3` is
in **06-llm-config**.

---

**End of Memory Bank index.**
