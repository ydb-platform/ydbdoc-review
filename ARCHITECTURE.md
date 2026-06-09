# Architecture — ydbdoc-review v2

This document describes the **v2 AST pipeline**. For narrative design rationale, see [Memory Bank](MEMORY_BANK.md).

**Diagram (overview):** [architecture.svg](architecture.svg) — component map next to [README.md](README.md).

## System context

```mermaid
flowchart TB
  subgraph external
    YDB_PR["ydb-platform/ydb PR\nlabels: doc_translate | doc_verify"]
    YC["Yandex AI Studio\nOpenAI-compatible API"]
  end

  subgraph entry
    ACT["GitHub Action\nDocker + entrypoint.sh"]
    CLI["CLI\nydbdoc-review run | verify"]
  end

  subgraph core["src/ydbdoc_review"]
    WF["github/workflow.py"]
    ORCH["pipeline/orchestrator.py"]
    TF["pipeline/translate_file.py"]
    NAV["pipeline/navigation_merge.py"]
    RPT["reporting/builder.py"]
    GIT["github/git_ops.py"]
  end

  subgraph output
    BR["branch ydbdoc-review/pr-N"]
    TPR["translation PR + reports"]
  end

  YDB_PR --> ACT
  YDB_PR --> CLI
  ACT --> WF
  CLI --> WF
  WF --> ORCH
  WF --> NAV
  ORCH --> TF
  TF --> YC
  NAV --> YC
  WF --> GIT
  WF --> RPT
  GIT --> BR --> TPR
```

## High-level flow

```mermaid
flowchart LR
  subgraph ingest
    GH[GitHub PR / git diff]
    PAIRS[RU/EN pairs + nav YAML]
  end
  subgraph per_file
    PARSE[parse_markdown]
    SEG[extract_segments]
    TR[translate_segments]
    REINS[reinsert_segments]
    CRIT[critic + verify]
    VAL[heuristics + gates]
    REND[render_markdown]
  end
  subgraph ship
    NAVM[navigation merge]
    GIT[branch ydbdoc-review/pr-N]
    RPT[PR comments + reports]
  end
  GH --> PAIRS --> PARSE --> SEG --> TR --> REINS --> CRIT --> VAL --> REND
  REND --> NAVM --> GIT --> RPT
```

## Package map

| Package | Responsibility |
|---------|----------------|
| `parsing/` | markdown-it-py + YFM plugins → pydantic AST (`Document`, block/inline nodes) |
| `segmentation/` | AST → `Segment` list; inline atoms protected as `⟦KIND:n⟧` placeholders |
| `rendering/` | AST → stable markdown (round-trip tests) |
| `translation/` | Glossary, prompt templates, translator + critic LLM calls |
| `validation/` | Structural checks, fence integrity, link locale, heuristics |
| `navigation/` | Scoped merge for `toc*.yaml` and redirect lists |
| `pipeline/` | `translate_file`, pair planning, PR orchestrator, completeness gate |
| `github/` | REST client, local git ops, `run_doc_translate` / `run_doc_verify` |
| `reporting/` | Markdown reports for source and translation PRs (§17) |
| `llm/` | Yandex AI Studio client (OpenAI SDK), retry, usage tracking |
| `config/` | `default.yaml` + env overrides + secrets |

## Per-file pipeline

`pipeline/translate_file.py`:

1. **Parse** source markdown to AST.
2. **Extract** translatable segments (headings, prose, table cells, note bodies, …).
3. **Translate** segments in char-budget batches (`translation/translator.py`); per-PR segment cache.
4. **Reinsert** translations into a copy of the AST (`segmentation/reinsert.py`).
5. **Finalize EN** — fence copy, Cyrillic fence comments, link locale, homoglyphs.
6. **Critic** reviews target text; applies fixes via `suggested_text` per `segment_id` (not find/replace).
7. **Verify** pass on unresolved issues (optional second critic call).
8. **Heuristics** — length, Cyrillic-in-EN, fence parity, anchors, nav-adjacent checks.
9. **Render** final markdown.

Flags: `enable_translate=False` for verify-only; `existing_target_text` for critic on existing EN.

## PR-level orchestration

`github/workflow.py` → `pipeline/orchestrator.py`:

1. **Enumerate** changed paths (`github/git_ops.list_local_changes` or GitHub API).
2. **Pair** `docs/ru/X.md` ↔ `docs/en/X.md`, locale `_includes`, nav YAML (`pipeline/pairs.py`).
3. **Plan** deterministic full re-translate from PR source (`pipeline/analyze.py`, §6.30).
4. **Run** `translate_file` per planned `.md` pair.
5. **Navigation merge** — scoped toc/redirect YAML (`navigation_merge.py`); upstream EN baseline for fork PRs (§6.44).
6. **Completeness** gate — every RU path in source PR diff must have an EN mirror (§6.32).
7. Partial failure skips file, continues PR.
8. **Git** — branch on upstream (`translation_branch_base`), commit written + deleted paths (§6.43), push.
9. **GitHub** — open/find translation PR, post short + full reports (`reporting/builder.py`).

## Configuration

- Packaged defaults: `config/default.yaml`.
- Override: `YDBDOC_LLM_*`, `YDBDOC_TRANSLATION_*`, `YDBDOC_PATHS_*`, …
- Secrets never in YAML: `YDBDOC_YC_FOLDER_ID`, `YDBDOC_YC_API_KEY`, `GITHUB_TOKEN`.

Model URI format: `gpt://<folder_id>/<model_slug>` (constructed in `YandexLLMClient`).

## GitHub Action

- **Image:** `Dockerfile` → `entrypoint.sh` → `ydbdoc-review run|verify`.
- **Inputs:** `repo`, `pr`, `merge_base_with`, `mode`, `dry_run`, `no_commit`.
- **Workspace:** docs repo mounted at `GITHUB_WORKSPACE`; entrypoint remaps runner paths.

## Extension points

| Change | Where |
|--------|--------|
| New YFM construct | `parsing/yfm_plugins/` + renderer + segmentation if translatable |
| New LLM role | `config/default.yaml`, `llm/client.py`, prompts under `prompts/v1/` |
| New heuristic | `validation/heuristics.py`, wire in `translate_file` |
| Report format | `reporting/builder.py`, Memory Bank §17 |
| Nav merge rule | `navigation/toc.py`, `pipeline/navigation_merge.py` |

## v1 vs v2

v1 (legacy on `main` before `doc-translate-ng`) used line/region masking and TOML config. v2 replaces
that with a structured AST, pydantic JSON schemas for LLM I/O, and YAML config.
The Action entrypoint interface (`action.yml` inputs) is preserved for the `ydb` repo.

## Testing layers

1. **Unit** — parser, segmentation, navigation, mocked LLM (`tests/unit/`).
2. **Fixture integration** — real doc pairs round-trip (`test_real_files_round_trip.py`).
3. **LLM smoke** — opt-in `@pytest.mark.llm` (`test_llm_smoke.py`).
4. **E2E on real PRs** — manual via `doc_translate` on labeled PRs in `ydb`.

Target: **90%+** line coverage on core packages (see Memory Bank §7).
