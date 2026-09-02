# FINAL-008 / R-016 v003: YFM token provenance closure

Resolve v001, then v002, then this amendment. This amendment closes only the discovered provenance contradiction: several YFM tokens required by the parser-owned validator currently have `map=None` and no `SourceSpan`, while v001 did not allow edits to their token-producing plugins. All v001/v002 semantics remain unchanged.

## Exact allowlist extension

Add only these production paths and symbols:

- new `src/ydbdoc_review/parsing/yfm_plugins/source_spans.py`: `utf8_source_span`;
- `src/ydbdoc_review/parsing/yfm_plugins/conditionals.py`: `_yfm_if_rule`;
- `src/ydbdoc_review/parsing/yfm_plugins/notes.py`: `_yfm_note_rule`;
- `src/ydbdoc_review/parsing/yfm_plugins/cuts.py`: `_yfm_cut_rule`;
- `src/ydbdoc_review/parsing/yfm_plugins/tabs.py`: `_yfm_tabs_rule`; remove its private `_span` and use `utf8_source_span`;
- `src/ydbdoc_review/parsing/yfm_plugins/includes.py`: `_yfm_include_rule`.

Add only these test paths to the v001 test allowlist: `tests/unit/test_yfm_conditionals.py`, `test_yfm_notes.py`, `test_yfm_cuts.py`, `test_yfm_tabs.py`, `test_yfm_includes.py`.

## Shared exact span constructor

`utf8_source_span(source: str, start_char: int, end_char: int) -> dict[str, int]` validates `0 <= start_char <= end_char <= len(source)` and returns exactly the existing `SourceSpan` payload keys: `byte_start`, `byte_end`, `line`, `column`. Bytes are `len(source[:position].encode("utf-8"))`; line/column are one-based and derived from that same prefix. It performs no regex, `find`, `rfind` or `index`. Plugins call it only with character offsets already owned by the active `StateBlock` rule.

## Exact token metadata at emission time

Every listed token receives `token.meta["source_span"]` while its block rule is consuming the source. Existing metadata is preserved.

- For any physical marker on line `L`, marker bounds are exactly `state.bMarks[L] + state.tShift[L]` through `state.eMarks[L]`; indentation and trailing newline are excluded.
- `yfm_if_open`: the physical `{% if ... %}` marker at `start_line`.
- each `yfm_if_branch_open`: its physical `if`, `elsif` or `else` marker at the already collected `marker_line`.
- each virtual `yfm_if_branch_close`: zero-width at `state.bMarks[branch_body_end] + state.tShift[branch_body_end]`, the start of the next branch marker or `{% endif %}`. It owns no source bytes and is structural only.
- `yfm_if_close`: the physical `{% endif %}` marker at the already discovered `close_line`.
- `yfm_note_open` / `yfm_note_close`: physical opening marker at `start_line` and physical `{% endnote %}` marker at `close_line`.
- `yfm_cut_open` / `yfm_cut_close`: physical opening marker at `start_line` and physical `{% endcut %}` marker at `close_line`.
- `yfm_tabs_open` / `yfm_tabs_close`: physical opening marker at `start_line` and physical `{% endlist %}` marker at `close_line`; retain existing `opening_span`/`closing_span` values byte-identically and add the same payload as `source_span`.
- `yfm_tab_open`: retain its existing full owned tab slice in `source_span` and its existing `title_span`.
- virtual `yfm_tab_close`: zero-width at `state.bMarks[body_end] + state.tShift[body_end]`, which is the next tab header or `{% endlist %}` boundary; keep `container_id`.
- `yfm_include`: its physical single-line directive at `start_line`.

Open-token `map` values that currently cover the whole container remain unchanged for Markdown-it compatibility. For these named token types, `markdown_parser._record_from_token_map` must prefer and validate `token.meta["source_span"]`; it must not reinterpret the whole-container `map` as the marker span. A virtual zero-width close is admitted to the structural container signature but omitted from `source_fence_config_signature`, exact-slice hashing and `residual_cyrillic_allowed_ranges` because it owns no bytes. Every non-zero physical marker is included once, in token order.

The existing plugin grammar regexes may continue solely inside their active `StateBlock` rules to recognize the line and discover nesting. They are parser consumption, not a later provenance scan. No new regex is allowed, and neither `markdown_parser`, the context builder nor validator may rescan `state.src`/document text to rediscover a marker.

## Fail closed

If a listed physical token lacks `meta.source_span`, has a zero-width span, or has a span outside the corresponding StateBlock-owned line, `parse_markdown_with_source_map` raises `source_map_incomplete:<token.type>`. If a listed virtual close lacks its exact zero-width boundary, it raises the same error. No fallback to `token.map` is allowed for a listed YFM token.

## Exact tests

Extend the five existing YFM test files with nested, indented and Cyrillic-marker-adjacent cases that assert exact UTF-8 byte bounds for every emitted open/branch/close/include token, including zero-width virtual close boundaries. Assert old tabs `opening_span`, `closing_span`, `title_span`, `source_span` and container IDs are preserved.

Extend `test_parser_round_trip.py` and `test_complete_document_validation.py` to prove:

1. all listed physical markers appear once in ordered non-prose records and virtual closes appear only in ordered structural records;
2. mutation of every opening, branch and closing marker changes the exact-slice hash and rejects;
3. nested conditionals/notes/cuts/tabs keep correct ownership order;
4. deleting or corrupting one plugin span fails closed and never falls back to `map` or raw search;
5. static AST inspection finds no new regex and no `.find`, `.rfind` or `.index` in `source_spans.py`, `_record_from_token_map`, `_build_parser_source_map`, the context builder or validator.

Add the five YFM test paths to the existing pytest and Ruff commands. No other requirement, file or implementation choice changes.
