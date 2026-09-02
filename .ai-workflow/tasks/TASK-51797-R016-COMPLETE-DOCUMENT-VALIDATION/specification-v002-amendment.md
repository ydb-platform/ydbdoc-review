# FINAL-008 / R-016 v002: close external findings

This amendment changes only the two RED findings in `response-v001.yaml`. Resolve `specification.md` first, then apply this document. It explicitly supersedes the affected v018 meanings; it is not merely a one-field schema addition.

## R016-V001-001: exact v018 supersession

The frozen field types from v018 remain, and `expected_anchors: tuple[str, ...]` is added after `expected_anchor_map`. The following four v018 field meanings are explicitly replaced for R-016:

1. `source_container_signature` remains `tuple[tuple[str, int, int], ...]`, but element 0 is the canonical structural descriptor `kind|level|tag|markup|variant`, not only the node kind. Start/end remain source UTF-8 provenance. Candidate equality compares the descriptor sequence, not offsets.
2. `source_fence_config_signature` remains `tuple[tuple[str, int, int, str], ...]`, but element 0 is the same canonical non-prose descriptor, not only the node kind. The hash remains SHA-256 of the exact source UTF-8 slice.
3. `ValidationAtomRecord.sha256` is SHA-256 of `_canonical_atom_payload`, not raw `placeholder.node.model_dump_json()`. The canonical payload is the structured node with only parser provenance removed and only `InlineLink.href` mapped through `_map_expected_destination`, serialized as compact sorted-key UTF-8 JSON. No other value is normalized.
4. `residual_cyrillic_allowed_ranges` is not used to mask candidate text. It is the exact `(start_byte, end_byte, descriptor)` projection, in order, of every parser-owned source non-prose record in `source_fence_config_signature`. Its validate-time role is closed below. Candidate prose is checked only through `extract_segments(candidate_document)`.

These are intentional corrections required to make FINAL-008 parser-owned and structured. All other v018 field types and meanings remain authoritative. The developer must not choose between v018 and v002.

## R016-V001-002: exact duties of retained fields

Insert these fail-closed checks into `validate_complete_document`:

### Non-prose range duty

Before candidate comparison, require

```python
tuple((start, end, descriptor) for descriptor, start, end, _sha in context.source_fence_config_signature)
    == context.residual_cyrillic_allowed_ranges
```

Otherwise reject with `validation_context_invalid:residual_cyrillic_ranges`. Then parse the candidate and require its ordered non-prose descriptors to equal the ordered descriptors obtained from those ranges and require every candidate exact-slice hash to equal the corresponding SHA in `source_fence_config_signature`. This is the sole allowlist for source-owned Cyrillic. Because `extract_segments` exposes only candidate prose and protected atoms are independently hash-checked, any Cyrillic visible to a translatable segment rejects with `residual_cyrillic_prose`; Cyrillic inside a matching non-prose/atom record is allowed. No byte ranges are projected from source positions onto candidate positions.

### EN TOC reachability duty

After exact ordered href equality and before anchor equality, process every candidate href from `expected_links` in order. If `context.en_toc_reachable is None`, skip only this reachability subcheck. Otherwise call the existing, unmodified `validation.glossary_toc_links.resolve_internal_md_href(context.source_file, candidate_href)` exactly once. `None` means external, fragment-only or non-Markdown and is exempt. A returned normalized target must be a member of `context.en_toc_reachable`; otherwise reject with `unreachable_en_internal_link:<target>`. Do not duplicate its path normalization, inspect raw Markdown, compare basenames or silently strip the link.

`resolve_internal_md_href` is a consumed existing contract and is not an allowed edit in this task.

## Ordered validator after v002

The exact order is now:

1. protect-token rejection;
2. context self-consistency projection check for residual ranges;
3. candidate parser/source-map success;
4. ordered structural descriptors/nesting;
5. canonical protected atoms;
6. ordered non-prose descriptor and exact-slice hashes, tied to the residual-range allowlist;
7. exact ordered structured href equality;
8. conditional EN TOC reachability via `resolve_internal_md_href`;
9. exact explicit-anchor sequence;
10. `extract_segments` residual-Cyrillic prose rejection.

Add exact tests:

- mutate the residual range projection alone and assert typed rejection;
- keep Cyrillic in an unchanged fenced-code record and assert acceptance, then move the same Cyrillic into prose and assert rejection;
- with `en_toc_reachable=None`, exact href parity remains required but reachability is skipped;
- with a reachable set, accept a normalized relative `.md#fragment` target in the set;
- reject the same target absent from the set with its normalized path in the error;
- prove external, fragment-only and non-Markdown targets return `None` and are exempt;
- monkeypatch/spy the existing resolver to prove one call per expected candidate href and no duplicate normalization.

No other file, symbol, lifecycle, command or forbidden-shortcut rule changes.
