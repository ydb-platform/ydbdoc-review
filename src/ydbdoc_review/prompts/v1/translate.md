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
- **Placeholders `⟦X{n}⟧`:** copy each marker exactly — same count, kind, and number as in the source. Markers stand for hidden inline atoms (code, links, variables) and are anchored to a specific concept in the source sentence (a table name, a column name, a type, …). You **may and should reorder markers** to follow natural word order in the target language — each marker must travel with the *same concept* it referred to in the source. Example — RU «к таблице ⟦C1⟧ колонку ⟦C2⟧ с типом ⟦C3⟧» → EN «column ⟦C2⟧ with data type ⟦C3⟧ to the ⟦C1⟧ table» (⟦C1⟧ is still the table, ⟦C2⟧ the column, ⟦C3⟧ the type, even though their left-to-right order changed). Never renumber markers (do not turn ⟦C1⟧ into ⟦C2⟧). If the same marker appears twice in the source, emit it twice in the output. Markers are not translatable. Never substitute `{{ variables }}`, `` `code` ``, or URLs for placeholders.
- **Links:** keep `[anchor](⟦U{n}⟧)` — translate anchor text only; never put a real URL in place of `⟦U{n}⟧` and never use a single `⟦L⟧` for the whole link. Keep `⟦V{n}⟧` (YFM variables) as plain text outside links — do not put `⟦V{n}⟧` in `](...)`.
- **Fenced code:** do not add or remove `` ``` `` / `~~~` markers; same count as the source segment.
- **Images:** keep `![alt](⟦S{n}⟧)` — translate alt text only; never replace `⟦S{n}⟧` with a path.
- Keep inline emphasis: `**bold**`, `*italic*` — but if the segment already uses `⟦C{n}⟧` for code, keep the placeholder, do not expose `` `code` ``.
- For table cells, keep `|` count and cell order; translate text inside cells only.
- YFM-only lines (`{% note %}`, `{% endlist %}`, etc.) — return verbatim if they appear in a segment.
- Empty `text` → empty `text` in output.

{style_guide_block}
