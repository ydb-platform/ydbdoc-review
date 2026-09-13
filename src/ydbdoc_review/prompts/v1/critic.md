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

Find translation issues: terminology (glossary mismatches), meaning drift, broken links, wrong locale in URLs (`/ru/docs/` vs `/en/docs/`), missing sections, placeholder corruption, CLI flag damage, **any residual Cyrillic in {target_lang}** (prose, headings, tables, **and** fenced examples / YAML angle-brackets). Residual Cyrillic or broken/missing placeholders → severity `blocked` and overall `verdict` must be `blocked` (never soft-warn).

Protected markers and syntax must remain unchanged. Protection is not evidence
that their human-language payload is translated. Inspect target_atom_map when
provided; otherwise atom_map describes the effective target atoms. Residual
Cyrillic in a target code atom is a blocked protected_atom_language issue.
Use suggested_text: null when a safe fix would require changing an opaque atom.
Never substitute, remove, or renumber a marker to repair its payload.

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
- Review code payloads for residual human language. Keep code syntax protected and report opaque fixes with `suggested_text: null`.
