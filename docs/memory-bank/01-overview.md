# Memory Bank — Overview & architecture

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

---

## 0. Pointers

```
.
├── src/ydbdoc_review/         # main Python package (v2 code)
│   ├── parsing/               # markdown → AST
│   ├── rendering/             # AST → markdown
│   ├── segmentation/          # AST ↔ translatable segments
│   ├── llm/                   # Yandex AI Studio client (✅ Phase C)
│   ├── translation/           # translator + critic (✅ Phase D)
│   ├── navigation/            # toc.yaml + redirect YAML scoped merge (D.1.5)
│   ├── validation/            # post-translation heuristics (✅ Phase E)
│   ├── pipeline/              # orchestration per-file and per-PR
│   ├── github/                # PR/branch/comment operations
│   ├── reporting/             # report builder
│   ├── config/                # default.yaml + loader (✅)
│   └── prompts/               # versioned prompts (v1/, ...) + glossary
├── tests/
│   ├── unit/                  # fast, no I/O
│   ├── integration/           # round-trip on real fixtures, LLM (local only)
│   └── fixtures/markdown_files/   # real YDB docs (committed)
├── scripts/                   # one-off utilities (fetch fixtures, smoke tests)
├── action.yml                 # GitHub Action manifest
├── Dockerfile                 # Docker image for the action (v2)
├── entrypoint.sh              # maps INPUT_* → ydbdoc-review CLI
├── docs/memory-bank/          # Memory Bank parts (see MEMORY_BANK.md index)
├── MEMORY_BANK.md             # index → docs/memory-bank/*.md
├── architecture.svg           # component diagram (next to README)
├── ARCHITECTURE.md            # developer architecture overview (v2)
├── CONTRIBUTING.md            # contributor guide
└── README.md                  # user-oriented overview (v2)
```

Important branch: **`doc-translate-ng`** — the v2 rewrite. To be merged into
`main` only after end-to-end tests pass on real PRs. Tag `v0.1.0` is used by the
`ydb` repo CI and is routinely moved forward with bug fixes (to avoid frequent
CI config edits). A separate tag (`v0.2.0`) can be used by external schedulers
Reactor/Nirvana during provider migration (§13.6).

---

---

## 1. Goal & non-goals

### 1.1. Goal

Build a reliable AST-based pipeline that translates YDB documentation between
Russian and English with **high quality and minimal hallucinations**, integrated
with GitHub Actions:

- Label `doc_translate` on a PR in `ydb-platform/ydb` → translate changed `.md`
  files, push to a separate branch `ydbdoc-review/pr-N`, open a separate
  translation PR.
- Label `doc_verify` on a translation PR → re-run QA (critic + heuristics) on
  the files on that branch; safe critic fixes are committed **on the same
  translation branch** (§6.75). Author/fork PRs still get a separate fixup PR (§6.64).

The reviewer should be able to see a clear verdict per file and merge the
translation PR with minimal manual fixes.

### 1.2. Non-goals (v2)

- **Multi-language**: only RU↔EN. Other languages are out of scope.
- **Live translation memory across PRs**: we don't keep a persistent cache
  between PRs. (Per-PR caching is implemented; see §6.)
- **Auto-merge of the translation PR**: humans always decide.
- **Translating non-`.md` content**: code in fenced blocks, YAML config files,
  PNGs etc. stay untouched.
- **Translating `{% include %}`'d content**: includes are pointers; if the
  included file is in the PR, it gets its own translation.

---

---

## 2. Why v2 (lessons learned from v1)

v1 (on `main` before `doc-translate-ng`) had several pipelines side by side
(`masked`, `legacy_annotated`, `legacy_line_json`, file-with-plan). They tried
to translate large file chunks with regex-based protection. In practice they
produced:

- **Dropped or hallucinated whole sections**: in failed PR #41736 the entire
  `## Пакетная потоковая обработка` was missing in EN; entire `## Batch
  streaming processing` was invented with different content.
- **Broken fenced code blocks**: ` ```bash ` was injected mid-prose, code was
  pulled out of blocks.
- **Damaged CLI flags**: `--input-framing` became `--input--framing` (model
  "fixed" a typo that wasn't there).
- **Untranslated Cyrillic words** bleeding into English text ("узла" inside
  a link in EN file).
- **A `fix-diff` mechanism** (`find` / `replace`) that almost never matched
  because the critic did not quote text exactly — out of 4 critic-proposed
  fixes in #41736, **all 4 were rejected** as non-matching.

**Root cause**: regex-based protection on raw markdown + whole-file LLM I/O +
string-based critic fixes are architecturally fragile. **v2 fixes this at the
architecture level**:

1. There is **no second pipeline**. There is only AST.
2. **LLM never sees fenced code blocks or YFM tags as raw text** — they are
   AST structure.
3. **Translator works on segments** (paragraphs / cells / list items) in JSON
   I/O, not on whole files.
4. **Critic returns structured `suggested_text` keyed by `segment_id`** —
   applied 1:1 to the segment. No string find/replace.
5. **Validation is structural** (placeholder counts, CLI-token sets, JSON
   schema) plus heuristic (length, Cyrillic-in-EN, fence parity), and is
   automatic.

---

---

## 3. Architecture overview

### 3.1. End-to-end data flow

```
GitHub PR in ydb-platform/ydb (label: doc_translate)
        │
        ▼
GitHub Action → Docker container → entrypoint.sh
        │
        ▼
ydbdoc-review CLI (mode=run)
        │
        ▼
[1] Pair RU↔EN files from PR diff (git merge-base)
        │
        ▼
[2] Pre-analyze (cheap model): which files actually need translation?
        │
        ▼
[3] For each file that needs translation:
        ┌─────────────────────────────────────────────────────┐
        │ a. parse_markdown(text) → Document (AST)            │
        │ b. extract_segments(ast) → list[Segment]            │
        │ c. chunk_segments(segs) → list[Batch]               │
        │ d. for each batch (in parallel, limit=3):           │
        │      translator LLM (JSON I/O) → translations[]     │
        │      validate placeholders + CLI tokens             │
        │      retry per-segment on failure                   │
        │ e. reinsert_segments(ast, translations) → new AST   │
        │ f. critic LLM (JSON I/O) in **batches** (same char budget as translator):
        │      segment pairs {source_text, translated_text} per batch
        │      issues[] with structured suggested_text
        │      apply suggested_text to segments
        │      re-validate critic pass (batched) → unresolved_issues[]
        │ g. heuristics: length, cyrillic-in-EN, fence parity │
        │ h. render_markdown(ast) → final markdown            │
        └─────────────────────────────────────────────────────┘
        │
        ▼
[4] Write all files, create branch `ydbdoc-review/pr-N`, push
[5] Open translation PR, post short comment in source PR
[6] Post full QA report (including heuristics) in translation PR
```

### 3.2. Hard architectural rules

1. **AST is the single source of truth.** No regex on raw markdown.
2. **LLM never sees raw code blocks or YFM tags.** Code blocks pass through
   untouched. YFM container tags are structural and never sent as text.
3. **The LLM only changes the inner text of segments.** Structure is
   reassembled from the original AST.
4. **No `find`/`replace` fixes from critic.** Critic returns structured
   `suggested_text` keyed by `segment_id`; we apply it 1:1.
5. **No whole-file LLM I/O** for translation; segments only, batched.
6. **Critic is in a different model family from translator** to avoid
   correlated blind spots.
7. **Job is green by default.** Errors at infrastructure level (no creds, push
   denied, code bug) fail. Translation quality issues don't fail the job;
   they are surfaced in the report. The user decides whether to merge.

---

---

[← Memory Bank index](../../MEMORY_BANK.md)
