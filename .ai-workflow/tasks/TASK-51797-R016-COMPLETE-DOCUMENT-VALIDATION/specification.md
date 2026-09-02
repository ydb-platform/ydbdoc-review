# FINAL-008 / R-016: parser-owned complete-document validation

Status: proposed for external specification review. This task closes only FINAL-008/R-016. It refines the approved v017/v018 validation contract; every unrelated approved requirement remains unchanged.

## Outcome

The same immutable validation context is built once from the RU source and used at every acceptance boundary. A candidate is accepted only when a fresh parser result proves that document structure, protected atoms, non-prose source slices, links, anchors and translated prose satisfy all invariants. Validation must consume parser/AST records. Searching raw Markdown with regular expressions, `find`, `index`, substring offsets or serialized-JSON replacement is forbidden.

## Exact production surface

Only these production files and symbols may implement R-016:

- `src/ydbdoc_review/parsing/markdown_parser.py`: `ParserSpanRecord`, `ParsedMarkdownSourceMap`, `_source_index_from_lines`, `_record_from_token_map`, `_build_parser_source_map`, `parse_markdown_with_source_map`, `parse_markdown`;
- `src/ydbdoc_review/translation/one_pass.py`: `ValidationAtomRecord`, `CompleteDocumentValidationContext`, `_walk_ast_preorder`, `_canonical_atom_payload`, `_map_expected_destination`, `build_complete_document_validation_context`, `validate_complete_document`, `translate_ru_to_en_once`, `OnePassResult`;
- `src/ydbdoc_review/translation/local_repair.py`: `run_bounded_local_repair` and the direct validator invocation;
- `src/ydbdoc_review/pipeline/translation_transaction.py`: the immediate pre-stage validator invocation.

Tests may change only in `tests/unit/test_parser_round_trip.py`, `tests/unit/test_complete_document_validation.py`, `tests/unit/test_one_pass_translation.py` and `tests/unit/test_translation_transaction.py` for this task. Policy-gate/control-artifact refresh remains owned by the parent remediation task.

## Parser-owned source map

Add frozen, slotted records:

```python
@dataclass(frozen=True, slots=True)
class ParserSpanRecord:
    kind: str
    start_byte: int
    end_byte: int
    descriptor: str

@dataclass(frozen=True, slots=True)
class ParsedMarkdownSourceMap:
    containers: tuple[ParserSpanRecord, ...]
    non_prose: tuple[ParserSpanRecord, ...]
```

`parse_markdown_with_source_map(text)` performs the Markdown-it parse once and returns the `Document` and source map derived from those same tokens. `parse_markdown(text)` delegates to it and returns only the `Document`; a second parser implementation is forbidden.

`_source_index_from_lines` computes UTF-8 byte starts once from `splitlines(keepends=True)`. `_record_from_token_map` converts only parser-owned `token.map` line bounds or an existing plugin-owned `SourceSpan` to byte bounds. Missing, reversed or out-of-range provenance for a required record raises `OnePassTranslationError("source_map_incomplete:<kind>")`; it never falls back to scanning source text.

Container records are emitted in Markdown-it token order for: paragraph, heading, blockquote, bullet/ordered list, list item, table, head/body/row/cell, YFM note, tabs/tab, conditional branch, cut and term-definition containers. `descriptor` contains token type, nesting level, tag, markup and source-owned structural variant/condition/title-presence fields, never translated prose or absolute candidate offsets. Candidate comparison uses ordered descriptors; source byte offsets are provenance, not equality keys.

Non-prose records are emitted in source order for front matter, fenced code, indented code, HTML block, include and YFM directive/config opening/closing records. Their exact UTF-8 source slice is hashed by the context builder. Fence marker, info string, body, config and closing marker are therefore protected without `_FENCE` or any other raw-text matcher.

## Exhaustive AST traversal

`_walk_ast_preorder` uses explicit `isinstance` branches for every `BlockNode` and `InlineNode` variant in `ast_types.py`. It yields containers, `InlineLink`, `Heading.anchor` and protected atoms in deterministic parser order. An unhandled node type raises `OnePassTranslationError("unhandled_ast_node:<type>")`. Generic `hasattr`, reflection over fields and implicit recursion are forbidden.

## Frozen context and exact semantics

Keep the approved frozen, slotted `ValidationAtomRecord`. Keep every approved v018 field in `CompleteDocumentValidationContext` and add exactly one field after `expected_anchor_map`:

```python
expected_anchors: tuple[str, ...]
```

This is the sole refinement of the v018 schema. It closes the previously unrepresented rule that an ASCII explicit anchor must remain byte-identical. Field meanings are:

- `source_container_signature`: `(descriptor, start_byte, end_byte)` in parser order;
- `source_atoms`: `(block_id, atom_id, sha256)` in segment/placeholder order;
- `source_fence_config_signature`: `(descriptor, start_byte, end_byte, sha256(exact UTF-8 slice))` in source order;
- `expected_links`: exact `(source_href, candidate_href)` pairs in AST preorder;
- `expected_anchor_map`: exact Cyrillic source-anchor to localized target-anchor pairs returned by anchor localization;
- `expected_anchors`: the sole allowed candidate explicit-anchor sequence in heading preorder: ASCII source anchors unchanged, Cyrillic source anchors replaced only by their exact map value;
- `residual_cyrillic_allowed_ranges`: parser-owned non-prose source spans only.

`build_complete_document_validation_context(source_text, source_file, segments, expected_anchor_map, en_toc_reachable=None)` is the only constructor. It parses the source, consumes its source map and protected segment records, makes no model call, and returns the frozen context.

## Structured links and atoms

`_map_expected_destination` uses `urllib.parse.urlsplit/urlunsplit`. It changes only an exact path prefix `/ru/` to `/en/`. It changes a fragment only when that fragment is an exact key in `expected_anchor_map` and the destination is the same document. Scheme, authority, remaining path, query, escaping and optional Markdown title are preserved exactly. String replacement over a URL, Markdown text or serialized JSON is forbidden.

`_canonical_atom_payload` operates on structured parser nodes. It removes only parser provenance fields (`source_span`), and for an `InlineLink` normalizes only its structured destination with `_map_expected_destination`. It serializes deterministic compact JSON with sorted keys and UTF-8, then hashes bytes. It must never call `.replace()` on `model_dump_json()` or equivalent serialized output. Candidate atom records must equal source records in count, order, IDs and hashes.

## `validate_complete_document`

Validation runs in this exact fail-closed order:

1. reject a protect token left in candidate text;
2. parse candidate through `parse_markdown_with_source_map`; parser/source-map failure rejects it;
3. compare ordered container descriptors, including nesting; no missing, extra, reordered or changed kind is allowed;
4. compare protected atom records exactly;
5. compare ordered non-prose descriptors and exact slice hashes;
6. compare `InlineLink.href` values in AST preorder against the candidate side of `expected_links`, including count/order;
7. compare explicit heading anchors in AST preorder exactly with `expected_anchors`; duplicates, additions and omissions reject;
8. call `extract_segments(candidate_document)` and reject Cyrillic in every translatable segment after protection-token validation.

Cyrillic is allowed only inside a byte-identical parser-classified non-prose slice already covered by step 5. Validation must not blank fences, links, inline code or other ranges with regex before checking Cyrillic.

## One-object lifecycle

Build one context in `translate_ru_to_en_once`, immediately after anchor localization and before the first complete-document validation. Add that exact object to `OnePassResult.validation_context`. The identical object (`is`, not merely equality) is used at four boundaries:

1. base candidate validation;
2. every candidate obtained by local repair before it can be selected;
3. the assembled candidate after each accepted insertion and again after each re-critic iteration;
4. `TranslationTransaction` immediately before `staged[output_path] = translated.text`.

No code path may reconstruct, mutate, convert to a dictionary or lazily derive the context. To avoid an import cycle while retaining the approved symbol location, remove the top-level import of local-repair functions from `one_pass.py` and import them inside `translate_ru_to_en_once` after the validator symbols exist. `local_repair.py` imports and directly calls `one_pass.validate_complete_document`; a caller-supplied replacement validator callback is forbidden.

On failure at any boundary: reject the current acquisition attempt, advance only through the already approved primary/fallback/repair bounds, and if exhausted raise the existing typed failure so the transaction rolls back. No invalid text may be rendered, inserted into a later repair prompt, staged or published.

## Required tests

`test_parser_round_trip.py` proves UTF-8 byte offsets with multibyte text, nested container order, YFM constructs, fence/directive complete spans and fail-closed missing provenance.

`test_complete_document_validation.py` contains a positive baseline and one isolated corruption for each: protect marker, parser failure, container kind/order/nesting, atom loss/duplication/reorder/content, link path/query/fragment/title/order, ASCII anchor mutation, Cyrillic anchor-map mismatch, fence marker/info/body/closing marker, front matter/YFM config/directive, and residual Cyrillic prose while identical Cyrillic inside a fence remains allowed. It also contains static AST inspection asserting the R-016 production symbols do not use `re`, `_FENCE`, `.find`, `.index` or string `.replace` for source ownership/URL/JSON normalization.

`test_one_pass_translation.py` proves invalid primary advances to fallback, invalid fallback exhausts/rolls back, invalid repair is never inserted, every re-critic candidate is revalidated, and all calls receive the same context identity.

`test_translation_transaction.py` proves the immediate pre-stage call uses `OnePassResult.validation_context` by identity and failure leaves no staged/published output.

Required commands:

```bash
.venv/bin/python -m pytest tests/unit/test_parser_round_trip.py tests/unit/test_complete_document_validation.py tests/unit/test_one_pass_translation.py tests/unit/test_translation_transaction.py -q
.venv/bin/python -m ruff check src/ydbdoc_review/parsing/markdown_parser.py src/ydbdoc_review/translation/one_pass.py src/ydbdoc_review/translation/local_repair.py src/ydbdoc_review/pipeline/translation_transaction.py tests/unit/test_parser_round_trip.py tests/unit/test_complete_document_validation.py tests/unit/test_one_pass_translation.py tests/unit/test_translation_transaction.py
```

## Forbidden shortcuts

No regex/raw-text source discovery; no `_FENCE`; no `find`, `rfind`, `index`, slicing guessed from substring positions; no top-level-child enumerate indexes as spans; no JSON or URL string replacement; no lossy token-count-only comparison; no full-document model validation; no new validation architecture/module; no optional bypass; no developer-selected alternative. Product implementation starts only after external `APPROVED`.
