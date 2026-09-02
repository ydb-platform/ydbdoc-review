# TASK-51797 one-pass final diff-conformance audit v001

## Verdict recommendation

**CHANGES_REQUESTED. The implementation is not conformant and must not be
committed, tagged, published, or used for a clean translation run.**

Audited worktree: `/private/tmp/ydbdoc-review-one-pass-v003` at base
`9ff8edec9a26d3975306e20adca325c6eb9f77e6`.

Normative contract: externally approved remediation v005 plus externally
approved amendments v006 through v016, together with the prerequisite
CHAT-ONCE v003, LLM-WIRING v002, ONE-PASS v010 and COVERAGE-DELETION v008
contracts. The audit read the complete tracked and untracked delta, the final
manifest and implementation report, and independently inspected the production
call paths below. Developer test counts were not treated as evidence of
semantic conformance.

The v016 policy command is GREEN and the manifest contains 187 unique paths.
That proves path/status/hash bookkeeping. It does not prove that a path mapped
to an R-ID implements that R-ID. Several manifest entries truthfully record
files whose final state directly violates the mapped requirement.

## Blocking findings

### FINAL-001, RED, R-001: mandatory same-file migration is still absent

`tests/unit/test_fence_comments.py` is deleted. The replacement remains the
differently named `tests/unit/test_fence_comments_read_only.py`. R-001 explicitly
requires retaining `test_fence_comments.py` as the mixed read-only file and
forbids replacing it solely with another filename. The manifest maps the
deleted final state to R-001, but mapping a deletion does not satisfy the
retain-in-place contract.

Required correction: restore the named file and complete the reviewed
same-file rewrite/move evidence without restoring retired production writers.

### FINAL-002, RED, R-003: TOC forwarding test still bypasses GitHub workflow

`tests/unit/test_one_pass_migration.py::test_translate_workflow_forwards_en_toc_reachable_to_read_only_validators`
constructs the sentinel itself and calls `run_pr_translation` directly. It does
not invoke `run_doc_translate` and does not make
`build_en_toc_reachable_from_repo` return the sentinel. This is the exact weak
test R-003 requires replacing.

Required correction: implement the production GitHub workflow test exactly as
R-003 specifies, without mocking an intermediate forwarding edge.

### FINAL-003, RED, R-008: locale-root workflow coverage was not restored

There is no `ydb/docs/ru/root-page.md` workflow test. The only locale-root
examples are dependency-queue unit tests using `root.md`; they do not prove
GitHub workflow queueing, EN counterpart path, provenance guard and atomic
publication.

Required correction: add the exact workflow-level root-page case from R-008.

### FINAL-004, RED, R-010: preserved placeholder suite is still deleted

`tests/unit/test_placeholder_repair.py` is deleted in the final worktree and the
manifest maps that deletion to R-010. R-010 requires restoring that original
file, preserving detector/blocking tests and deleting only writer expectations
after same-file GREEN evidence.

Required correction: restore and split the original suite exactly as specified;
retain the table-cell corruption/rollback equivalence.

### FINAL-005, RED, R-011: navigation still uses shared chat and RU fallback

`src/ydbdoc_review/pipeline/navigation_merge.py::_translate_menu_labels` remains
reachable from `merge_navigation_pair`. It calls
`client.chat(messages, role="translate")`, parses JSON locally, fills missing
translations with the RU label, and on malformed JSON returns every RU label as
success. No `TranslationJobManifest`, fixed model pair or
`AcquisitionController` is present on this call path.

Required correction: replace this exact production path with the reviewed
translation-local fixed pair and bounded acquisition. Invalid/exhausted output
must block the transaction with zero publication and never become RU success.

### FINAL-006, RED, R-012: repair candidates are accepted before protocol gates

In `translation/local_repair.py`, the parser passed to
`AcquisitionController(role="repair")` checks only finding ID, block ID and a
non-empty string. Token/atom equality, allowed-range checks, unique replacement
location and `global_validate(proposed)` execute only after acquisition has
returned an accepted result. Therefore an invalid primary does not advance by
the required protocol-invalid transition before acceptance.

Required correction: place every reviewed repair-candidate gate inside the
acquisition parser, before it can return a payload. A rejected candidate must
never be inserted or counted as accepted.

### FINAL-007, RED, R-014: critic payload discloses protected document bytes

`run_bounded_local_repair` sends `{"document": current, "block_records": ...}`
to the critic. `current` is the complete rendered document and includes
source-owned code, configuration and directive bytes. This contradicts the
required secret non-disclosure test and the minimal protected context contract.

Required correction: build the reviewed minimal critic representation from
editable prose blocks and verified atom metadata so unique code/config/directive
secrets cannot appear in any critic or repair request.

### FINAL-008, RED, R-016: complete global validator does not exist

No production symbol named `validate_complete_document` exists. The one-pass
entrypoint passes only `assert_no_protect_token` as `global_validate`. Thus the
base render and repaired candidates are not centrally checked for parser
success, Markdown/YFM parity, source-owned atom hashes/order, link/href/fragment/
anchor rules, fence/config equality and residual Cyrillic as required.

Required correction: implement the single read-only validator and call it at
all four reviewed boundaries, with one corruption test per invariant.

### FINAL-009, RED, R-021: legacy translation skip implementation/tests remain

`src/ydbdoc_review/pipeline/skip_paths.py` still defines
`filter_translate_changes` and `filter_path_set`, including the
`translate_skip_globs` contract, and `tests/unit/test_translate_skip_paths.py`
still asserts that public-material Markdown is dropped. Although the main
workflow no longer calls these helpers, R-021 requires removal of the old RU
Markdown skip-glob parsing/defaults/tests, not merely disconnection from one
call path.

Required correction: delete the retired translation-skip helpers and their
legacy tests, preserving only explicitly specified navigation-only exclusion
behavior.

## Non-blocking documentation finding

### FINAL-010, YELLOW, R-009: final report omits the required behavior record

The code has `NOTE_TITLE`, `CUT_TITLE` and typed `UnknownSegmentKindError`
handling, but `implementation-report-v010.md` does not record these decisions or
their exact tests as R-009 requires. Correct the report after the blocking code
changes and final verification.

## Requirements independently found conformant or not contradicted

The following items had no blocking contradiction in this audit:

- R-002: forbidden `_restore_source_hrefs` and its `_HREF` writer are absent;
- R-004: report remains NOT READY; v016 validate is GREEN; manifest path/status/
  hashes match the complete current delta;
- R-005: the public fragment mapper is reachable only from read-only href and
  redirect validators in the inspected source;
- R-006: strict tabs implementation and approved negative-fixture amendments
  are present;
- R-007: ops isolation is explicit and an in-memory lifecycle workflow test is
  present;
- R-009 production behavior: note/cut title and unknown-kind code is present,
  subject to FINAL-010 report correction;
- R-013: ordered ID list plus `Counter` validation occurs before mapping;
- R-015: `InlineStrike` and parser-owned paired structural atoms are present;
- R-017: translation acquisition explicitly propagates cancellation and other
  control-flow exceptions;
- R-018: typed Yandex normalization implements the reviewed priority ordering;
- R-019: dependency discovery traverses parser-populated link/include spans and
  has no dependency-layer regex scanner;
- R-020: structured provenance types/report plumbing are present.

These observations do not waive independent tester execution after the RED
findings are fixed.

## Manifest and frozen-baseline result

- base commit: exact expected value;
- immutable snapshot: accepted by the v016 resolver and policy gate;
- manifest: 187 unique whole-delta entries and GREEN normalized self-hash;
- actual tracked delta: 55 modified plus 43 deleted paths;
- actual untracked delta: 89 paths;
- `uv.lock`: absent;
- frozen control and baseline bytes: policy validation GREEN;
- manifest truthfulness conclusion: structurally truthful, semantically
  insufficient. FINAL-001 and FINAL-004 are examples where a truthful `D`
  entry is mapped to a requirement that forbids that final state.

## Required next cycle

Do not send this implementation to tester as a release candidate. First obtain
external review of this audit. If the findings are confirmed, the analyst must
issue a closed remediation amendment containing exact file/symbol/test changes
for FINAL-001 through FINAL-010. Only then may a restricted developer implement
those corrections. After that: analyst whole-diff re-audit, independent tester,
external implementation review, and only an APPROVED conformant result may
proceed to commit, tag and translation rerun.
