Review a {source_lang} → {target_lang} translation batch from one YDB documentation file.

File: `{file_path}`  
Batch: {batch_index} of {batch_count} (segment ids in this batch only)

## Segment pairs

Each item has `source_text` ({source_lang}) and `translated_text` ({target_lang}) for the same structural segment.

```json
{batch_json}
```

## Task

Find translation issues **only in the segments listed above**: terminology (glossary mismatches), meaning drift, broken links, wrong locale in URLs (`/ru/docs/` vs `/en/docs/`), placeholder corruption, CLI flag damage.

Return **only** JSON:

```json
{
  "verdict": "ok",
  "issues": [
    {
      "segment_id": "s0042",
      "severity": "warning",
      "category": "terminology",
      "comment": "short explanation",
      "suggested_text": "corrected segment text or null"
    }
  ]
}
```

Rules:
- `verdict`: `ok` | `warnings` | `blocked` — for **this batch only**
- `segment_id` must match an id from the batch when the issue is localized
- `suggested_text` is the **full corrected translated_text** for that segment (placeholders intact), not a diff. Use `null` if you cannot propose a safe fix
- Report discrete issues only; do not rewrite segments that are fine
- Code segments (`kind` code/fence): do not change except comments (`#`, `--`) still in {source_lang}
