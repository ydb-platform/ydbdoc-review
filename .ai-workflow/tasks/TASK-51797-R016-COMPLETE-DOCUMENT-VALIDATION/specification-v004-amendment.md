# FINAL-008 / R-016 v004: translatable YFM title spans

Resolve v001, v002 and v003, then apply this amendment. It closes only the conflict between byte-identical physical-marker hashing and the already approved translation of `YfmNote.title` / `YfmCut.title` as `NOTE_TITLE` / `CUT_TITLE`. All other semantics remain unchanged.

## Exact parser record refinement

Explicitly supersede the v001 `ParserSpanRecord` schema by adding exactly one final field:

```python
@dataclass(frozen=True, slots=True)
class ParserSpanRecord:
    kind: str
    start_byte: int
    end_byte: int
    descriptor: str
    translatable_spans: tuple[tuple[int, int, str], ...] = ()
```

Each tuple is absolute UTF-8 `(start_byte, end_byte, segment_kind)` provenance inside this record. It is parser metadata, not a candidate mask. Only `yfm_note_open` may contain segment kind `note_title`; only `yfm_cut_open` may contain `cut_title`; all other records have the empty tuple. Spans must be ordered, non-overlapping and contained in the record. Zero width is valid only for a syntactically present empty quoted title. Any violation fails with `source_map_invalid_translatable_span:<token.type>`.

## Exact plugin provenance

The already allowed v003 files and symbols receive these exact additions:

- `conditionals.py`, `tabs.py`, `includes.py`: no semantic change for v004;
- `notes.py::_yfm_note_rule`: when regex group 2 is present, including an empty `""`, set `token.meta["title_span"] = utf8_source_span(state.src, pos + m_open.start(2), pos + m_open.end(2))` on `yfm_note_open`; when the optional quoted title is absent, omit `title_span`;
- `cuts.py::_yfm_cut_rule`: always set `token.meta["title_span"] = utf8_source_span(state.src, pos + m_open.start(1), pos + m_open.end(1))` on `yfm_cut_open`, including an empty quoted title.

The span excludes the surrounding quote characters. Therefore opening `{%`, directive name, note type, whitespace, both quotes and closing `%}` remain protected. These offsets come from the regex match that already recognized the active StateBlock line; no later regex, `find`, `index` or text rescan is permitted.

`markdown_parser._record_from_token_map` reads these exact metadata spans while consuming the same tokens and emits `("note_title")` or `("cut_title")` in `translatable_spans`. It never derives a title span from the title value or searches source text. A note/cut AST title without the corresponding syntactically required metadata span, or metadata on any other token, fails closed.

## Canonical structural marker digest

Add in `markdown_parser.py` exactly `_canonical_source_slice(source_text: str, record: ParserSpanRecord) -> bytes`. It operates on UTF-8 bytes and record-owned byte offsets only:

1. validate all translatable spans as above;
2. append every byte from the record slice outside those spans unchanged;
3. replace each allowed span with the fixed ASCII bytes `b"\x00YDBDOC_TRANSLATABLE:" + segment_kind.encode("ascii") + b"\x00"`;
4. return the result.

No decoding, regex, `find`, `index`, length preservation or placeholder derived from translated text is allowed. `_build_parser_source_map` or the context builder hashes `sha256(_canonical_source_slice(...))` for `yfm_note_open` and `yfm_cut_open`. Candidate validation computes the same canonical digest from the candidate parser record. Thus only title bytes may change; syntax, directive type, note type, whitespace, quotes, attributes and delimiters must remain byte-identical.

This explicitly supersedes v002's raw exact-slice hash only for `yfm_note_open` and `yfm_cut_open` records with parser-owned title spans. Every other non-zero physical marker keeps SHA-256 of its exact raw UTF-8 slice. Ordered descriptor equality and all v002 residual-range duties remain unchanged.

The candidate title itself remains subject to the existing AST segment rules: it must be extracted as exactly one `NOTE_TITLE`/`CUT_TITLE` segment, contain no protect marker and pass residual-Cyrillic prose validation. Canonical marker hashing does not authorize omission, duplication or Cyrillic retention.

## Exact tests

Extend `test_yfm_notes.py` and `test_yfm_cuts.py` to assert exact multibyte UTF-8 `title_span` offsets for titled, empty-title and (notes only) absent-title syntax. Assert quote bytes are outside the title span.

Extend `test_parser_round_trip.py` and `test_complete_document_validation.py` with these exact cases:

1. English title replacement of different byte length preserves the canonical digest and is accepted;
2. mutation of note type, whitespace, either quote, directive name or `%}` changes the digest and rejects;
3. title span moved outside the opening record, overlapped, attached to another token, missing for an AST title, or synthesized for absent note title fails closed;
4. empty quoted titles receive a zero-width translatable span and canonical sentinel; absent note title receives no span;
5. title Cyrillic remaining in candidate rejects through extracted prose even though structural digest matches;
6. static AST inspection proves `_canonical_source_slice` has no regex/`find`/`rfind`/`index`, and note/cut title provenance uses only `m_open.start/end` plus `pos`.

No product path, symbol, model call, lifecycle boundary, URL rule or other validator invariant changes.
