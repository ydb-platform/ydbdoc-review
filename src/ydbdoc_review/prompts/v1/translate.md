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
- **Placeholders `⟦X{n}⟧`:** preserve every marker exactly, with the same multiplicity and strictly the same left-to-right order. Never reorder, renumber, omit or duplicate markers. Never expose or guess their hidden code, variables or URLs.
- **Links:** keep `[anchor](⟦U{n}⟧)` — translate anchor text only; never put a real URL in place of `⟦U{n}⟧` and never use a single `⟦L⟧` for the whole link. Keep `⟦V{n}⟧` (YFM variables) as plain text outside links — do not put `⟦V{n}⟧` in `](...)`.
- **Fenced code:** do not add or remove `` ``` `` / `~~~` markers; same count as the source segment.
- **Images:** keep `![alt](⟦S{n}⟧)` — translate alt text only; never replace `⟦S{n}⟧` with a path.
- Keep inline emphasis: `**bold**`, `*italic*` — but if the segment already uses `⟦C{n}⟧` for code, keep the placeholder, do not expose `` `code` ``.
- For table cells, keep `|` count and cell order; translate text inside cells only.
- YFM-only lines (`{% note %}`, `{% endlist %}`, etc.) — return verbatim if they appear in a segment.
- Empty `text` → empty `text` in output.

{style_guide_block}
