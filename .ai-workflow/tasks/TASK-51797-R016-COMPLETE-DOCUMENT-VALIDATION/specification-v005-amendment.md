# FINAL-008 / R-016 v005: translatable front-matter value spans

Resolve v001 through v004, then apply this amendment. It closes only the conflict between raw hashing of the complete `front_matter` record and the existing extraction/reinsertion of selected front-matter strings. All other contracts remain unchanged.

## Exact selected values

The sole key source is the existing `parsing.front_matter.TRANSLATABLE_FRONT_MATTER_KEYS`, currently exactly `("title", "description")`. A top-level value is translatable if and only if `translatable_front_matter_fields` already selects it: the key is in that tuple, the YAML value is a string and `value.strip()` is non-empty. Empty strings, nulls, lists, maps, aliases, duplicate selected keys and every other key are non-translatable and remain raw-protected. No second key list is allowed.

## Exact production extension

Add `src/ydbdoc_review/parsing/front_matter.py` to the production allowlist with exactly these new/refined symbols:

```python
@dataclass(frozen=True, slots=True)
class FrontMatterValueRecord:
    key: str
    style: str
    value: str
    start_byte: int
    end_byte: int
    block_header_end_byte: int | None
    body_indent: bytes
    newline: bytes

def parse_front_matter_with_spans(raw: str) -> tuple[dict[str, Any], tuple[FrontMatterValueRecord, ...]]: ...
def _encode_front_matter_value(record: FrontMatterValueRecord, value: str) -> bytes: ...
```

`parse_front_matter` delegates to `parse_front_matter_with_spans` and returns the mapping only. `translatable_front_matter_fields` consumes that same mapping/records contract. `apply_front_matter_updates` is refined to perform descending-offset surgical replacement through `_encode_front_matter_value`; it must not call `dump_front_matter` or serialize the whole mapping. `dump_front_matter` remains available only for its existing standalone API/tests, not for one-pass reinsertion.

## YAML-owned spans

`parse_front_matter_with_spans` uses PyYAML `yaml.compose` / parser nodes and their `start_mark`/`end_mark` plus scalar `style`; it does not locate keys or values with `_KEY_LINE`, regex, `find`, `rfind` or `index`. It walks only the top-level `MappingNode`, requires `ScalarNode` keys, rejects duplicate selected keys, and matches the selected scalar node directly to its parser marks.

The style and replaceable value bytes are exact:

- plain (`node.style is None`): the scalar node's complete `[start_mark.index, end_mark.index)` bytes;
- single quoted (`"'"`): bytes strictly between the parser-owned opening and closing quote, i.e. `start+1` through `end-1`;
- double quoted (`'"'`): bytes strictly between the parser-owned opening and closing quote;
- literal/folded (`"|"` / `">"`): the YAML header line, including style marker, chomping/indent indicators and any header comment, is protected. `block_header_end_byte` is the byte start of the following line obtained from the parser mark's line number and a one-pass `splitlines(keepends=True)` byte-start table. The replaceable span is `[block_header_end_byte, end_mark)` and is the scalar body only.

For block scalars, `body_indent` is the exact leading byte prefix of the first non-empty body line, obtained by a forward byte loop bounded by the parser-owned body span; if all body lines are empty, it is the key indentation plus two ASCII spaces. `newline` is exactly `b"\r\n"` or `b"\n"` from the first parser-owned line ending, falling back to `b"\n"` only when the scalar ends at EOF without any line ending. For non-block styles both fields are `b""`. No search primitive is used.

All indexes are converted from PyYAML character marks to UTF-8 byte offsets through one precomputed character-to-byte boundary table. Direct source substring searching is forbidden. The full front-matter Markdown token span, including both `---` delimiter lines, is obtained from its Markdown-it `token.map`; YAML-relative records are shifted by the parser-owned body-start byte.

For the `front_matter` `ParserSpanRecord`, `translatable_spans` contains `(absolute_start, absolute_end, "front_matter:<key>")` for selected records in YAML mapping order. Its descriptor contains the ordered `(key, style)` pairs for selected values. It contains no translated value. Missing/misaligned marks, unsupported YAML node shapes, overlaps, duplicate selected keys or a selected semantic value without exactly one record fail with `source_map_invalid_front_matter:<key>`.

## Canonical digest and surgical reinsertion

`_canonical_source_slice` applies the already approved v004 sentinel algorithm to these parser-owned spans. This explicitly supersedes v002 raw hashing only for the selected value bytes inside the `front_matter` record. Everything outside them remains in the canonical byte stream and therefore byte-exact: both `---` delimiters, keys, colon/spacing, quote characters, scalar style/header, untranslated values, comments and key order.

`_encode_front_matter_value` preserves the selected record's style:

- plain: UTF-8 encode the new value verbatim;
- single quoted: YAML single-quote content encoding, doubling each embedded single quote;
- double quoted: deterministic YAML double-quote content encoding using `json.dumps(value, ensure_ascii=False)[1:-1]`;
- literal: split the requested semantic value with `splitlines()`; emit each physical content line as `body_indent + UTF-8 line + newline`, retaining the source value's final-newline presence. Folded: require the requested value to contain no interior newline; emit one physical content line with the same indent/newline and final-newline presence as the source value. The original header controls clip/strip/keep semantics. The mandatory full reparse must yield exactly the requested semantic value; otherwise raise `front_matter_translation_requires_style_change:<key>`. Never change style/header.

After all descending replacements, parse the complete candidate with `parse_front_matter_with_spans` and require: selected semantic values equal the requested updates; every unselected semantic value equals the source; selected key order/style equals the source; canonical digest equals the source. Otherwise fail and roll back. This is mandatory even when there is one update.

The existing `_KEY_LINE`/`front_matter_key_order` may remain only for the legacy standalone `dump_front_matter` API. They are forbidden in span construction, surgical reinsertion and complete-document validation.

## Exact tests

Add `tests/unit/test_front_matter.py` to the allowed test paths and required pytest/Ruff commands. Cover `title` and `description` in plain, single-quoted, double-quoted, literal and folded styles with Cyrillic and multibyte characters. Required outcomes:

1. a different-byte-length English value is GREEN and preserves delimiters, key bytes, colon/spacing, quote/style/header, comments, unselected fields and order byte-for-byte;
2. mutation of a key, delimiter, colon/spacing, quote, block header/chomping indicator, comment, order or any unselected value is RED;
3. empty/non-string/unselected values have no translatable span and any mutation is RED;
4. duplicate selected keys, malformed marks, overlapping/out-of-range spans and impossible style-preserving emission fail closed;
5. title/description residual Cyrillic is still RED through extracted prose;
6. static AST inspection proves span/reinsert/validator paths use no regex, `find`, `rfind`, `index`, whole-map dump or raw-text key/value search.

No other allowed file, lifecycle boundary, model behavior or invariant changes.
