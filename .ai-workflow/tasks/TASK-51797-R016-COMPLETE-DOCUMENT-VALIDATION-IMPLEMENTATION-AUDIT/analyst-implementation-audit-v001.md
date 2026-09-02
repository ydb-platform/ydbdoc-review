# FINAL-008 / R-016 implementation audit v001

Verdict recommendation: **FAIL / CHANGES_REQUIRED**.

Audited the literal worktree `/private/tmp/ydbdoc-review-one-pass-v003` against resolved approved v001-v005. No product code was edited by the analyst.

## RED findings

### FINAL008-IMPL-001: physical tab marker syntax is not protected

`src/ydbdoc_review/parsing/markdown_parser.py::_build_parser_source_map` explicitly excludes `yfm_tab_open` from non-prose records (`token.type not in {"yfm_tab_open", "yfm_include"}`). Its container descriptor records only title presence, not the physical list marker. This contradicts v003's rule that every physical marker occurs once in ordered non-prose records.

Reproduction: source tab header `- Русский` validates candidates `+ English` and `* English` as accepted. Required change: parser-owned `yfm_tab_open.title_span` must become a v004-style translatable span inside a physical header record whose canonical digest protects the list marker, spacing and line boundary while neutralizing only title bytes. Add exact `tab_title` role/schema/tests; do not hash its current full body-owned `source_span`.

### FINAL008-IMPL-002: complete-document failure does not advance translation acquisition fallback

`translation/one_pass.py::translate_ru_to_en_once` lets `AcquisitionController.acquire()` accept after `_parse_translate_response`; rendering, context construction and `validate_complete_document` happen only after acquisition has returned. A structurally invalid primary response therefore aborts the operation instead of being rejected by the acquisition parser and advancing to the approved translation fallback. This violates v001 lifecycle and the required `invalid primary advances to fallback / invalid fallback exhausts` tests.

Required change: close the acquisition boundary so each primary/fallback payload is rendered/localized and complete-validated before acquisition acceptance, without rebuilding the one frozen context or adding calls. Preserve exactly one accepted render and rollback after exhaustion.

### FINAL008-IMPL-003: selected front-matter YAML aliases are mis-owned, not rejected

v005 explicitly makes aliases non-translatable/raw-protected. `parse_front_matter_with_spans` accepts `x: &a Hello\ntitle: *a\n` and emits the `title` span over bytes 3..11, which belong to `&a Hello` under unselected key `x`. Surgical title update would mutate the unselected anchor definition.

Required change: use YAML node ownership to reject an alias-backed selected value with `source_map_invalid_front_matter:title`; it must never reuse the anchored node's marks. Add alias and cross-key-anchor tests.

### FINAL008-IMPL-004: invalid/misattached translatable metadata is not fail-closed

`_canonical_source_slice` accepts `note_title`, `cut_title` or any `front_matter:*` role on any record; `_record_from_token_map` silently ignores `title_span` metadata on token kinds other than note/cut. Physical listed YFM spans are also permitted to be zero-width. v003/v004 require role-to-token ownership, reject metadata on every other token, and reject zero-width physical marker spans.

Required change: enforce an exact role matrix (`note_title` only `yfm_note_open`, `cut_title` only `yfm_cut_open`, `front_matter:<selected-key>` only `front_matter`, new tab role only `yfm_tab_open`); reject foreign metadata; reject zero-width physical token spans while retaining zero-width virtual close/title-empty exceptions.

### FINAL008-IMPL-005: mandatory evidence suite was not implemented

`tests/unit/test_complete_document_validation.py` is only 93 lines and covers a small subset. `test_parser_round_trip.py` adds no provenance tests. Note/cut/conditional/include test files add no required v003/v004 span tests. `test_front_matter.py` adds no v005 span/style/corruption tests. There is no exact same-object identity test across all four boundaries and no invalid-primary/fallback complete-validation test.

Missing required classes include: exhaustive container nesting, atom loss/dup/reorder/hash, href path/query/fragment/title/order, parser failure, every fence/config/front-matter/YFM marker mutation, multibyte exact spans for every YFM token, missing/corrupt metadata no-fallback, note/cut empty/absent title distinction, all front-matter styles and protected mutations, alias rejection, forbidden-code AST assertions, and transaction/local-repair same-context identity.

Required change: implement every resolved v001-v005 test. Existing GREEN tests do not waive absent gates.

### FINAL008-IMPL-006: literal front-matter required GREEN matrix is incomplete in behavior

Manual check: `apply_front_matter_updates('title: |\n  Привет\n  мир\n...', {'title': 'A much longer English title'})` raises `front_matter_translation_requires_style_change:title`. v005 requires a different-byte-length selected value GREEN for literal style and an exact all-style test matrix. No test exercises this path.

Required change: make the approved style-preserving literal algorithm satisfy the required different-length case while retaining header/indent/newline semantics and full reparse/digest equality; add the exact case.

## Gates run

- Focused pytest over parser, complete validator, one-pass, transaction, front matter and five YFM suites: **161 passed**. This is insufficient because mandatory cases above are absent.
- Ruff over every allowed production/test path: **passed**.
- Literal runtime proof for tab marker: `-`, `+`, `*` candidates all accepted: **RED**.
- Literal runtime proof for alias: selected `title` record points into unselected anchor bytes: **RED**.
- Literal runtime proof for block scalar different-length value: typed rejection: **RED against required GREEN matrix**.

## Conformance summary

Implemented correctly in the inspected code: frozen context schema, parser-owned YFM spans for if/note/cut/tabs/include, note/cut match-group title spans, structured URL mapping, expected anchor sequence, conditional EN TOC membership, residual-range self-check, local-repair candidate validation and immediate transaction pre-stage validation. These partial successes do not override the six RED findings.
