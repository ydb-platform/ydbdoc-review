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
- **Placeholders `⟦X{n}⟧`:** copy each marker exactly; same count, kind, number, and order as input. They are not translatable — only translate prose around them. Never substitute `{{ variables }}`, `` `code` ``, or URLs for placeholders.
- **Links:** keep `[anchor](⟦U{n}⟧)` — translate anchor text only; never put a real URL in place of `⟦U{n}⟧` and never use a single `⟦L⟧` for the whole link.
- Keep inline emphasis: `**bold**`, `*italic*` — but if the segment already uses `⟦C{n}⟧` for code, keep the placeholder, do not expose `` `code` ``.
- For table cells, keep `|` count and cell order; translate text inside cells only.
- YFM-only lines (`{% note %}`, `{% endlist %}`, etc.) — return verbatim if they appear in a segment.
- Empty `text` → empty `text` in output.

{style_guide_block}
