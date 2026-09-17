Review a {source_lang} → {target_lang} translation batch from one YDB documentation file.

File: `{file_path}`  
Batch: {batch_index} of {batch_count} (segment ids in this batch only)

## Segment pairs

Each item has `source_text` ({source_lang}) and `translated_text` ({target_lang}) for the same structural segment. `atom_map` lists source payloads (e.g. `code:episodes`, `url:mvcc.md`); `target_atom_map`, when provided, lists actual target payloads under aligned marker names. Matching names identify corresponding slots, whose payloads can differ. Source Cyrillic alone is not evidence of untranslated target content.

```json
{batch_json}
```

## Task

Find translation issues **only in the segments listed above**: terminology (glossary mismatches), meaning drift, broken links, wrong locale in URLs (`/ru/docs/` vs `/en/docs/`), placeholder corruption, CLI flag damage, **any residual Cyrillic in {target_lang}** (prose, inline `` `…` ``, **and** fenced code / YAML placeholders like ``<SID по умолчанию>``). Residual Cyrillic or wrong/missing placeholders → severity `blocked`; batch `verdict` must be `blocked` (never soft-warn these).

**Do not** flag placeholder issues when the source and target maps show the same atoms under aligned marker names but **word order** differs in {target_lang} prose (e.g. RU "к таблице ⟦C1⟧ колонку ⟦C2⟧" vs EN "column ⟦C2⟧ to ⟦C1⟧ table" after alignment). Flag placeholder corruption only when an atom is **wrong, missing, or substituted** (e.g. `Uint64` where `views` should be). Tool-localized human-language notation in the target map is allowed.

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
- `segment_id` must match an id from the batch when the issue is localized
- `suggested_text` is the **full corrected translated_text** for that segment (placeholders intact), not a diff. Use `null` if you cannot propose a safe fix
- Report discrete issues only; do not rewrite segments that are fine
- Review code payloads for residual human language. Keep code syntax protected and report opaque fixes with `suggested_text: null`.
