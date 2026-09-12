Re-translate one {source_lang} → {target_lang} documentation segment using **critic feedback**.

File: `{file_path}`  
Segment: `{segment_id}` (`{segment_kind}`)  
Path: `{segment_path}`

## Source ({source_lang})

```
{source_text}
```

## Current translation ({target_lang})

```
{current_translation}
```

## Critic issues to fix

```json
{critic_issues_json}
```

## Task

Return **only** JSON:

```json
{{"segments": [{{"id": "{segment_id}", "text": "improved translation"}}]}}
```

Rules:
- Address every critic issue above while keeping structural validation rules.
- Every `⟦…⟧` placeholder from the source must appear in the **same order** with the **same** kind and number (`⟦C1⟧`, `⟦U2⟧`, etc.). Do not add, remove, rename, or reorder placeholders.
- Translate Russian prose to {target_lang}; keep CLI flags, code, URLs inside placeholders unchanged.
- For table cells: preserve `|` structure; translate cell text only.
- Do not add or remove fenced-code markers (`` ``` ``); count must match the source segment.
- `⟦V{{n}}⟧` stays in prose; `⟦U{{n}}⟧` stays only inside `[text](⟦U{{n}}⟧)`.
- When critic provides `suggested_text` for this segment, prefer it if it satisfies the rules above.

{style_guide_block}
