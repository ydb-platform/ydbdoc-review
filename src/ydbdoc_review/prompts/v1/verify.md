Re-verify a {source_lang} → {target_lang} translation after fixes were applied.

File: `{file_path}`

## Source ({source_lang})

```
{source_text}
```

## Current translation ({target_lang})

```
{translated_text}
```

## Previously reported issues (may be fixed)

```json
{prior_issues_json}
```

## Segment index

```json
{segments_index_json}
```

Return **only** JSON — same schema as the critic pass:

```json
{
  "verdict": "ok",
  "issues": []
}
```

Protected markers and syntax must remain unchanged. Protection is not evidence
that their human-language payload is translated. Inspect target_atom_map when
provided; otherwise atom_map describes the effective target atoms. Residual
Cyrillic in a target code atom is a blocked protected_atom_language issue.
Use suggested_text: null when a safe fix would require changing an opaque atom.
Never substitute, remove, or renumber a marker to repair its payload.

List only **unresolved** issues. If a prior issue was fixed correctly, omit it. Use `suggested_text` for any remaining fixable problem.
