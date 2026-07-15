Translate the following **glossary** segments from {source_lang} to {target_lang}.

File: `{file_path}` (glossary profile)

## Input

```json
{batch_json}
```

## Output

Return **only** a JSON object of the same shape:

```json
{"segments": [{"id": "s0001", "text": "translated text"}, ...]}
```

Rules (glossary-specific):
- One input segment → one output segment (do not merge or split).
- **Term lines:** when the source lists `**RU**`, **EN**`, `**RU** или **EN**`, or comma/`или`-separated bold synonyms, produce **English-only bold terms** in the translation (see system message). Example — RU `**Кластер** или **cluster** {{ ydb-short-name }} представляет…` → EN `A {{ ydb-short-name }} **cluster** is…`.
- **Synonym lists:** RU `**A**, **B**, **C** или **D**` → EN `**A**, **B**, or **D**` (English labels only; drop Cyrillic duplicates).
- **Headings** (`kind: heading`): translate title text; preserve `{#anchor-id}` exactly.
- **Links:** keep `[anchor](⟦U{n}⟧)` — translate anchor text; Wikipedia URLs are fixed later by tooling.
- **Placeholders, fences, tables, YFM:** same rules as standard translation (see system message).

{style_guide_block}
