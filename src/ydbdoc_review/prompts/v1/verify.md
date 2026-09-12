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

List only **unresolved** issues. If a prior issue was fixed correctly, omit it. Use `suggested_text` for any remaining fixable problem.
