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

## QA

1. **Critic** — full RU file + full EN file (`05_verify_translation.txt`).
2. **Repair** — at most **one** whole-file fix (`06_fix_translation.txt`) if the critic listed blockers.
3. **Translator** — checklist: each critic item fixed? (`07_confirm_repair.txt`).

No per-section repair loop, no repeated full-file retranslate in `deterministic_prepare`.

## Ops

- Logs: `[ydbdoc-fm] pipeline-v2 unit 3/42 | prose | …`
- `YDBDOC_PIPELINE=legacy` — old behaviour
