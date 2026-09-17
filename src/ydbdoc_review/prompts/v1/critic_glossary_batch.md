Review a {source_lang} → {target_lang} translation batch from the YDB **glossary** (`concepts/glossary.md`).

File: `{file_path}`  
Batch: {batch_index} of {batch_count} (segment ids in this batch only)

## Segment pairs

```json
{batch_json}
```

## Task

Find translation issues **only in the segments listed above**.

**Glossary-specific expectations (do NOT flag as errors):**
- English output uses **English-only bold terms**; Cyrillic term names from the RU source are intentionally omitted from bold lists.
- Multiple English synonyms in bold (`**tenant nodes** or **compute nodes**`) are correct.

Residual Cyrillic anywhere in EN, including protected code payloads, is **blocked**.
`atom_map` describes source atoms; `target_atom_map`, when provided, describes actual target payloads under aligned marker names. Compare both maps without treating source Cyrillic as target evidence.

**Do flag:** meaning drift, broken internal links, wrong `/ru/docs/` locale, placeholder corruption, CLI damage, untranslated RU prose, Cyrillic left in running text.

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
- `verdict`: `ok` | `warnings` | `blocked` — for **this batch only**
- `segment_id` must match an id from the batch when localized
- `suggested_text` is the **full corrected translated_text** for that segment (placeholders intact)
- Avoid `terminology` issues that merely restate “RU term missing from bold list” — that is by design for EN glossary
