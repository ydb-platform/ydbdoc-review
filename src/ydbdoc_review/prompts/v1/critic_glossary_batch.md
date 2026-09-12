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
- Residual Cyrillic in EN prose (outside placeholders/code) is still **blocked**.

**Do flag:** meaning drift, broken internal links, wrong `/ru/docs/` locale, placeholder corruption, CLI damage, untranslated RU prose, Cyrillic left in running text.

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
