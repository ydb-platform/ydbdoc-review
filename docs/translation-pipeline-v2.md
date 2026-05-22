# Translation pipeline v2

Default since `YDBDOC_PIPELINE=v2` (set `legacy` to restore the old loop).

## Translate

1. Parse RU markdown into ordered **units** (`document_segments.py`):
   - `prose` — text and headings (`#`, `###`, paragraphs)
   - `table` — markdown tables (split out of prose)
   - `fence` — ` ``` … ``` ` (code copied; RU comments translated in **one batch** per fence)
   - `diplodoc` — `{% note %}…{% endnote %}`, `{% cut %}…{% endcut %}`

2. **One FM call per unit** (`pipeline_v2.translate_unit`), then `assemble_document_units`.

3. Light post-process: link restore + deterministic CLI fixes (no cyrillic repair storm).

After QA, pipeline v2 runs the same **CLI-only** pass (not legacy `deterministic_prepare_en`).

## QA (per file, pipeline v2 only)

| Step | FM calls | What |
|------|----------|------|
| Critic | **1** | Whole RU + whole EN in one request (`05_verify_translation.txt`) |
| Repair | **0–1** | Whole-file fix only if critic listed blockers (`06_fix_translation.txt`) |
| Translator | **1** | Verdict ПРИНЯТЬ / НЕ ПРИНИМАТЬ (`07_confirm_repair.txt`) |

**No** `deterministic_prepare_en`, **no** cyrillic-repair loops, **no** per-section QA in v2.

Optional: `YDBDOC_QA_REPAIR_MAX_ROUNDS` (default **0**) — extra repair→translator after НЕ ПРИНИМАТЬ.

Default **`YDBDOC_TRANSLATION_STRICT_MERGE=1`**: НЕ ПРИНИМАТЬ → CI red, коммит не создаётся.

## Ops

- Logs: `[ydbdoc-fm] pipeline-v2 unit 3/42 | prose | …`
- `YDBDOC_PIPELINE=legacy` — old behaviour
