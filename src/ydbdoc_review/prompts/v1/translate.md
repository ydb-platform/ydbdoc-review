Translate the following document segments from {source_lang} to {target_lang}.

File: `{file_path}`

## Input

```json
{batch_json}
```

## Output

Return **only** a JSON object of the same shape:

```json
{"segments": [{"id": "s0001", "text": "translated text"}, ...]}
```

Rules:
- Translate **only** the `text` field. Do **not** change `id`.
- One input segment → one output segment (do not merge or split).
- Preserve markdown inline markup inside `text`: `**bold**`, `` `code` ``, `[link text](url)`, `{{ variables }}`.
- For table cells, keep `|` count and cell order; translate text inside cells only.
- YFM-only lines (`{% note %}`, `{% endlist %}`, etc.) — return verbatim if they appear in a segment.
- Empty `text` → empty `text` in output.

{style_guide_block}
