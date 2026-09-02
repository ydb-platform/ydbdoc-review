# FINAL-008 / R-016 v006: freeze context only after accepted candidate

Resolve v001–v005 and externally confirmed `FINAL008-IMPL-007`.

## Exact lifecycle refinement

Inside `translate_ru_to_en_once`'s translation acquisition parser:

1. For each primary/fallback attempt, localize Cyrillic explicit anchors for
   that attempt's candidate and build an attempt-local
   `CompleteDocumentValidationContext` with that attempt's `candidate_anchor_map`.
2. Call `validate_complete_document(candidate, attempt_local_context)`.
3. Only after that call succeeds, freeze the accepted context into the one
   immutable object used by later local-repair and transaction pre-stage
   boundaries.
4. Rejected attempts must not populate or retain the frozen context. A
   subsequent fallback therefore cannot be checked against the rejected
   primary's expected anchors.

Do not create a second long-lived context architecture, weaken explicit-anchor
parity, accept invalid bytes, or rebuild the frozen object after acceptance.

## Exact adversarial test

Add
`tests/unit/test_one_pass_translation.py::test_invalid_primary_cyrillic_anchor_does_not_poison_fallback_context`:

- RU: `# Русский {#якорь}` plus one paragraph;
- invalid primary: `Primary Heading` + `- Broken structure` with map
  `якорь -> primary-heading`;
- valid fallback: `Fallback Heading` + `English text.` with map
  `якорь -> fallback-heading`;
- assert acceptance of the fallback text/anchor and
  `result.validation_context.expected_anchors == ("fallback-heading",)`.

## Related atom fix

`_map_expected_destination` must look up both the raw and `urllib.parse.unquote`
forms of a same-document fragment against `expected_anchor_map`, so
percent-encoded Cyrillic source fragments canonicalize to the accepted English
anchor.
