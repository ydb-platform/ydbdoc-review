# FINAL-008 / R-016 implementation audit v002

Verdict recommendation: **FAIL / CHANGES_REQUIRED**.

Audited the literal worktree `/private/tmp/ydbdoc-review-one-pass-v003` against approved v001-v005 and externally confirmed findings FINAL008-IMPL-001..006. No product code was edited by the analyst.

## Disposition of the six confirmed findings

- **FINAL008-IMPL-001 CLOSED:** `yfm_tab_open` now produces a physical non-prose record with a parser-owned `tab_title` translatable span. `+` and `*` mutations are rejected by the focused test.
- **FINAL008-IMPL-002 PARTIALLY CLOSED, superseded by FINAL008-IMPL-007:** complete-document validation now runs inside the acquisition parser and invalid primary content advances to the translation fallback. The new placement contains a stale-context defect described below.
- **FINAL008-IMPL-003 CLOSED:** selected front-matter aliases are rejected by YAML node ownership instead of reusing the anchored node's bytes.
- **FINAL008-IMPL-004 CLOSED:** the role/token matrix and physical zero-width checks are fail-closed, including the new `tab_title` role.
- **FINAL008-IMPL-005 OPEN:** the mandatory v001-v005 evidence matrix remains materially incomplete.
- **FINAL008-IMPL-006 CLOSED:** plain, quoted, literal and folded selected values now accept a different-byte-length English value in the focused style matrix.

## RED findings

### FINAL008-IMPL-007: fallback is validated against the rejected primary's anchor map

`translate_ru_to_en_once` lines 491-528 creates `frozen_context` during the first acquisition parser call, before that candidate has passed `validate_complete_document`. The context is built with that attempt's `candidate_anchor_map`. When the primary candidate is structurally invalid, acquisition correctly advances, but the fallback reuses the rejected primary's expected anchors rather than its own parser-owned localized anchor map.

Independent runtime reproduction:

- RU input: `# Русский {#якорь}` plus one paragraph;
- invalid primary: heading `Primary Heading`, paragraph `- Broken structure`, localized map `якорь -> primary-heading`;
- otherwise valid fallback: heading `Fallback Heading`, paragraph `English text.`, localized map `якорь -> fallback-heading`;
- actual result: `OnePassTranslationError: translation_acquisition_exhausted`, with calls to both translation models;
- required result: accept the structurally valid fallback and freeze/use one context whose anchor expectations describe the accepted candidate, without accepting invalid bytes or rebuilding context at later critic/repair/pre-stage boundaries.

This is a production correctness failure, not merely a missing test. Add the exact adversarial test above. The analyst must resolve the lifecycle contract before a developer chooses an implementation; do not invent a second context architecture or weaken explicit-anchor parity.

### FINAL008-IMPL-005: mandatory evidence suite remains incomplete

The added tests cover the six earlier reproductions, but do not implement the exhaustive evidence expressly required by approved v001-v005. Missing or non-exhaustive required gates include:

1. parser round-trip UTF-8 byte offsets, nested ownership order, complete fence/directive spans, and fail-closed missing provenance;
2. isolated parser failure, container kind/order/nesting corruptions, atom duplication, link title/order and independent path/query/fragment corruptions, Cyrillic anchor-map mismatch, every fence marker/info/body/closing mutation, and every front-matter/YFM config/directive mutation;
3. every listed YFM opening/branch/closing/include token in nested, indented and Cyrillic-adjacent cases, including exact multibyte bounds, virtual close boundaries, mutation of each marker, and missing/corrupt metadata proving no `map` or raw-search fallback;
4. note/cut titled, empty-title and absent-title distinctions, exact quote-excluding spans, and each syntax/whitespace/quote/directive/terminator corruption;
5. front-matter `title` and `description` across all five styles, with independent key/delimiter/colon-spacing/quote/block-header/chomping/comment/order/unselected-value corruptions, duplicate selected keys, malformed/overlapping/out-of-range spans and residual Cyrillic;
6. invalid repair never inserted, every re-critic candidate revalidated, same context identity at all approved boundaries, and transaction rollback/no staged output on final validation failure;
7. the full forbidden-shortcut AST gate, including `re`, `_FENCE`, `find`, `rfind`, `index`, and string replacement across every production symbol/file named by v001-v005.

Passing broad legacy tests cannot waive these explicit acceptance gates. Implement the exact approved tests, not a representative subset.

## Independent evidence

- Runtime stale-anchor fallback proof: **RED**, deterministic acquisition exhaustion after primary and fallback calls.
- Literal code inspection confirms fixes for tab marker provenance, alias rejection, role ownership/zero-width validation and style-preserving front matter.
- Focused pytest over parser, complete validator, one-pass, transaction, front matter and all five YFM suites: **passed** (197 collected cases).
- Ruff over every resolved v001-v005 production/test path: **passed**.
- `tests/unit/test_complete_document_validation.py` remains a representative 163-line subset; the approved amendments require an exhaustive mutation/provenance matrix across the listed test files.

## Required next step

The analyst must publish a closed amendment for FINAL008-IMPL-007 that reconciles per-attempt Cyrillic anchor localization with the one-object lifecycle. Then the developer implements only that approved amendment plus the already explicit missing test matrix. External implementation review remains mandatory.
