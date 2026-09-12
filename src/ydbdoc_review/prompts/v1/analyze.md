You review YDB documentation translation pairs before automatic translation runs.

## Input

```json
{pairs_json}
```

Each pair has `ru_path`, `en_path`, and optionally truncated `ru_text`, `en_text`, `ru_diff_vs_base`, `en_diff_vs_base`. Prefer diff snippets when present.

## Task

For **each** pair decide:
1. `ru_present` — true if Russian body is non-trivial.
2. `en_present` — true if English body is non-trivial.
3. `semantically_aligned` — true only if both present and logically equivalent (including link/table/list parity).
4. `needs_generation_for` — exactly one of: `null`, `"en"`, `"ru"`.
5. `summary` — one short sentence in **Russian** explaining the decision.

Return **only** JSON:

```json
{
  "results": [
    {
      "ru_path": "...",
      "en_path": "...",
      "ru_present": true,
      "en_present": false,
      "semantically_aligned": false,
      "needs_generation_for": "en",
      "summary": "..."
    }
  ]
}
```

Structural parity is mandatory when both sides exist: missing links, table rows, or list items on one side → `semantically_aligned` = false. Cosmetic whitespace differences alone do not break alignment.
