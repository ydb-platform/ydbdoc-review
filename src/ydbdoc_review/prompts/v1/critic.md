Review a {source_lang} → {target_lang} translation of one YDB documentation file.

File: `{file_path}`

## Source ({source_lang})

```
{source_text}
```

## Translation ({target_lang})

```
{translated_text}
```

## Segment index (for `segment_id` references)

```json
{segments_index_json}
```

## Task

Find translation issues: terminology (glossary mismatches), meaning drift, broken links, wrong locale in URLs (`/ru/docs/` vs `/en/docs/`), missing sections, placeholder corruption, CLI flag damage.

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
- `verdict`: `ok` | `warnings` | `blocked`
- `segment_id` must match an id from the segment index when the issue is localized.
- `suggested_text` is the **full corrected text** for that segment (with placeholders intact), not a diff. Use `null` if you cannot propose a safe fix.
- Do not rewrite the whole file; report discrete issues only.
- Code inside fenced blocks: do not change except comments (`#`, `--`) still in {source_lang}.
