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
- `blocked` — must not merge until fixed (e.g. wrong meaning, broken placeholders)
- List only **unresolved** issues whose `segment_id` is in this batch. If a prior issue was fixed correctly, omit it
- Use `suggested_text` for any remaining fixable problem (full corrected `translated_text` for that segment, or `null`)
