# Memory Bank — Operations

> Part of the [Memory Bank index](../../MEMORY_BANK.md).  
> Authoritative design doc for **ydbdoc-review v2** (`doc-translate-ng`).

---

## 19. Logging

### 19.1. Library

`rich.logging.RichHandler` for human-friendly local output; plain stdout for
CI (Actions captures stdout fine).

### 19.2. Levels

- **INFO**: high-level progress (per file, per phase).
- **DEBUG**: per-batch, per-segment events; LLM request/response trimmed.
- **WARNING**: retry attempts, heuristic flags, fallback model used.
- **ERROR**: unrecoverable per-file failures (still don't fail the job).

### 19.4. Docker image (GitHub Action)

The Action uses `action.yml` → `Dockerfile`; GitHub **rebuilds the image on each run**
from the commit behind the ref (e.g. `ydb-platform/ydbdoc-review@v0.1.0`). No local
`docker build` is required for ydb workflows.

After changing translation/validation code, move the tag workflows use:

```bash
git tag -f v0.1.0 HEAD && git push -f origin v0.1.0
```

Label `org.opencontainers.image.revision` records the git SHA in the Dockerfile for debugging.

---

## 20. Cost tracking

### 20.1. Per-call tracking

Every `llm.chat()` call records:
- `model`, `input_tokens`, `output_tokens`, `latency_ms`, `retries`, `success`.

### 20.2. Aggregation per PR

Sum tokens and approximate cost. Cost calculation uses a hard-coded price
table per model slug (updated manually). Yandex AI Studio doesn't return
prices in headers as of writing.

### 20.3. Reporting

Cost block appears in the full report (translation PR) and the short summary
(source PR).

### 20.4. Backlog: persistent cost log

`docs-internal/cost-log.md` (in `ydbdoc-review` repo) maintained by a script
that appends one line per PR run. Not in MVP.

---

---

## 21. Glossary of terms used in this Memory Bank

- **AST / IR**: our pydantic representation of a parsed markdown document.
- **Segment**: a translatable unit extracted from AST (a paragraph, a heading,
  a table cell, a list item, etc.).
- **Placeholder / marker**: `⟦C1⟧`, `⟦L1⟧`, etc., representing a protected
  inline atom in the LLM-visible text.
- **Batch**: a group of segments sent to the LLM in one request.
- **Round-trip**: parse → render → equal (or idempotent after first pass).
- **Identity**: extract → re-insert with no changes → equal to direct render.
- **Translation PR**: the PR created by `doc_translate` against the source
  PR's HEAD branch.
- **Source PR**: the PR in `ydb-platform/ydb` that the user labeled.
- **Verify**: re-running QA on a translation PR via `doc_verify` label.
- **YFM**: Yandex Flavored Markdown — the markdown superset Diplodoc parses.
- **Diplodoc**: open-source documentation framework by Yandex.

---

**End of Memory Bank.**

---

[← Memory Bank index](../../MEMORY_BANK.md)
