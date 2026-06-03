Repair a failed {source_lang} → {target_lang} translation for **one** documentation segment.

File: `{file_path}`  
Segment: `{segment_id}` (`{segment_kind}`)  
Path: `{segment_path}`

## Validation error

```
{validation_error}
```

## Source ({source_lang})

```
{source_text}
```

## Failed attempt ({target_lang}), if any

```
{failed_attempt}
```

## Task

Return **only** JSON:

```json
{"segments": [{"id": "{segment_id}", "text": "corrected translation"}]}
```

Rules:
- Output **must** pass structural validation: every `⟦…⟧` placeholder from the source appears in the **same order** with the **same** kind and number (`⟦C1⟧`, `⟦U2⟧`, etc.). Do not add, remove, rename, or reorder placeholders.
- Translate Russian prose to {target_lang}; keep CLI flags, code, URLs inside placeholders unchanged.
- For table cells: preserve `|` structure; translate cell text only.
- Do not add or remove fenced-code markers (`` ``` ``); count must match the source segment.
- `⟦V{n}⟧` stays in prose (e.g. “on the ⟦V1⟧ server”); `⟦U{n}⟧` stays only inside `[text](⟦U{n}⟧)`.
- If you cannot produce a valid segment, return the source text unchanged (still with all placeholders correct).

{style_guide_block}
