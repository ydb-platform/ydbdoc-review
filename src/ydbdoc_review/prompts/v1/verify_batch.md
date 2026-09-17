Re-verify a {source_lang} → {target_lang} translation batch after fixes were applied.

File: `{file_path}`  
Batch: {batch_index} of {batch_count}

## Segment pairs (current translation)

```json
{batch_json}
```

## Previously reported issues for this batch (may be fixed)

```json
{prior_issues_json}
```

Protected markers and syntax must remain unchanged. Protection is not evidence
that their human-language payload is translated. Inspect target_atom_map when
provided; otherwise atom_map describes the effective target atoms. Residual
Cyrillic in a target code atom is a blocked protected_atom_language issue.
Use suggested_text: null when a safe fix would require changing an opaque atom.
Never substitute, remove, or renumber a marker to repair its payload.

Return **only** JSON — same schema as the critic pass:

```json
{
  "verdict": "ok",
  "issues": []
}
```

Rules:
- `verdict`: **only** `ok` | `warnings` | `blocked` — for **this batch only** (do not use `needs_fix`, `issues_found`, etc.)
- `ok` — no unresolved problems in this batch
- `warnings` — non-blocking issues remain
- `blocked` — must not merge until fixed (wrong meaning, leftover/broken `⟦…⟧` protect markers, **any** residual Cyrillic in EN including code fences / YAML examples)
- List only **unresolved** issues whose `segment_id` is in this batch. If a prior issue was fixed correctly, omit it
- Use `suggested_text` for any remaining fixable problem (full corrected `translated_text` for that segment, or `null`)
