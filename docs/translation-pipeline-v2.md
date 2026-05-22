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

## QA

1. **Critic** — full RU file + full EN file (`05_verify_translation.txt`).
2. **Repair** — whole-file fix (`06_fix_translation.txt`) when the critic listed blockers.
3. **Translator** — checklist (`07_confirm_repair.txt`).
4. If **НЕ ПРИНИМАТЬ** — repeat repair (using critic + «оставшиеся проблемы») → translator, up to `YDBDOC_QA_REPAIR_MAX_ROUNDS` (default **2**).

Default **`YDBDOC_TRANSLATION_STRICT_MERGE=1`**: остаётся «НЕ ПРИНИМАТЬ» → CI red, **коммит не создаётся** (без ручной правки EN).

No per-section repair loop, no `deterministic_prepare` storm after QA.

## Ops

- Logs: `[ydbdoc-fm] pipeline-v2 unit 3/42 | prose | …`
- `YDBDOC_PIPELINE=legacy` — old behaviour
