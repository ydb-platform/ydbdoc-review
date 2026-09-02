# TASK-51797 one-pass implementation conformance audit v002

## Verdict

**RED: implementation is not conformant and must not be committed, tagged, or
used for a production translation.** Passing tests do not cure the deviations
below because several required gates were deleted or replaced with weaker tests.

Audited worktree: `/private/tmp/ydbdoc-review-one-pass-v003`, diff against its
current `HEAD` base. Normative documents: CHAT-ONCE `chat-once-v003`, LLM-WIRING
`llm-wiring-v002`, ONE-PASS `one-pass-v010`, and COVERAGE-DELETION
`coverage-deletion-v008`, each with its configured external `APPROVED` response.

Coverage-deletion v008 is externally `APPROVED` in `response-v008.yaml` dated
2026-09-02. Findings below concern violation of that approved contract, not a
missing approval.

## Blocking findings

### AUDIT-001: required same-file migration order was not followed

Coverage-deletion v008 requires every blocker suite to collect and pass after a
same-file rewrite before deletion, then requires traceable unchanged moves.
The diff instead directly deletes:

- `tests/unit/test_fence_comments.py`;
- `tests/unit/test_glossary_verify_alignment.py`;
- `tests/unit/test_harness.py`;
- `tests/unit/test_harness_pair_toc_reachable.py`;
- `tests/unit/test_harness_pr.py`;
- `tests/unit/test_verify_partial_realign.py`;
- `tests/unit/test_verify_realign_cap.py`;
- `tests/harness/test_regression_cases.py` and all three case fixture sets.

There is no auditable intermediate same-file GREEN state in the worktree and no
unchanged traceable move for the deleted tests. A new
`test_fence_comments_read_only.py` is not the same-file migration required by
v008. This is a direct process and coverage violation, not an implementation
detail.

Required correction: restore the deleted blocker tests/fixtures from base,
perform the exact v008 same-file rewrites, prove them GREEN, then move only the
approved bodies with legacy path/test IDs. Do not restore production harness or
writers to make the tests pass.

### AUDIT-002: link atoms are restored twice, with an unauthorized regex writer

`translation/one_pass.py:92-102` defines `_restore_source_hrefs`. After protected
link boundaries have already been restored through `restore_inline_text`, it
collects hrefs from RU with `_HREF`, pairs them positionally with regex matches
in rendered EN, rewrites `/ru/` to `/en/`, and mutates the document again.

This contradicts the authoritative ownership rule: complete Markdown link
wrappers are source-owned atoms, restored once from the parser representation;
post-translation href mutation is forbidden except the explicitly constrained
staged Cyrillic-anchor transaction. Regex/position matching is exactly the
class of mechanism that lost links in TASK-51797 and does not safely represent
nested labels, escaped destinations, titles, duplicate/reordered links, or YFM
autotitle syntax.

Required correction: remove `_restore_source_hrefs` and its call. Make the
parser/protect/restore representation itself produce the exact permitted href
atom, including a separately specified deterministic locale-path mapping if
mapping `/ru/` to `/en/` is required. Add failing regression tests before code.

### AUDIT-003: the mandatory production TOC-forwarding test is weaker than v008

`tests/unit/test_one_pass_migration.py:212-242` does not execute the GitHub
workflow and does not spy `build_en_toc_reachable_from_repo`. It constructs a
sentinel itself and calls `run_pr_translation` directly. It proves transaction
forwarding only, not the required production chain:

`GitHub workflow -> build_en_toc_reachable_from_repo -> v010 transaction -> all
applicable read-only validators`.

The implementation report nevertheless claims that production workflow
forwarding is covered. The original harness TOC test was deleted, so the useful
workflow plumbing guarantee currently has no equivalent test.

Required correction: implement the exact v008 production test without mocking
the forwarding edges. Spy the builder result and all applicable validators,
invoke `run_doc_translate`, and assert the identical sentinel set reaches them.

### AUDIT-004: implementation report overstates authorization and coverage

`implementation-report-v010.md:22` says deleted tests/fixtures were “listed by
coverage-deletion-v008”, but v008 lists them under conditional
delete-after-same-file-GREEN rules which were not followed. Lines 25 and 39-48
claim preserved read-only coverage and readiness based on a green suite while
the gate tests themselves were deleted. The report also unilaterally waives
repository-wide Ruff despite v008 completion criterion requiring Ruff GREEN.

Required correction: mark the report NOT READY; enumerate deviations and exact
test deletions. A focused `F`-only Ruff run may replace full Ruff only after an
analyst specification amendment and external approval. “313 pre-existing” is
evidence for a proposed exception, not authority to create one.

### AUDIT-010: additional mixed read-only coverage was deleted wholesale

The diff deletes `tests/unit/test_placeholder_repair.py` entirely although the
v008 cluster disposition explicitly preserves placeholder detection/blocking
assertions and retires only writer assertions. It also removes
`test_critic_fix_survives_table_cell_render_round_trip` from
`test_renderer_coverage.py` without a named table-cell local-repair/atom-parity
replacement. General prose local-repair tests do not prove table-cell token
round-trip.

Required correction: restore the placeholder test file, split preserved
read-only detection/blocking from retired writer cases, and add a traceable
v010 table-cell repair or blocking transaction test before removing the legacy
renderer assertion.

### AUDIT-005: fragment-retirement equivalence is incomplete

`test_fragment_repair.py` was heavily rewritten, but v008 requires every
remaining writer-oriented legacy test to become a parametrized, traceable case
with its original name as a case ID. The final file contains only a small set of
free-standing replacements and deletes most original identities. The required
same-file before/after GREEN evidence and complete case mapping are absent.

`_remap_fragment_via_ru_en_pages` remains as a pure read-only helper and is
called by `href_parity` and `redirect_impacts`. Keeping a pure validator helper
is defensible and does not itself mutate EN, but v008 names only the retained
read-only detector contract. Its cross-module private API retention must be
explicitly accepted or converted to a public read-only validator API.

Required correction: restore traceable cases and inventory every old fragment
test. Keep the pure mapper only after external review confirms it is read-only,
does not affect translation/rendering, and is required by preserved validators.

## Unapproved implementation decisions requiring analyst specification

### AUDIT-006: broad YFM-tabs parser policy

`markdown_parser.py:424-444` assigns every non-list sibling after a tab list to
the preceding tab and leading siblings to the first tab. ONE-PASS requires
lossless YFM handling, so a parser fix may be necessary, but this ownership rule
for malformed/unindented bodies is a new grammar decision. It can silently move
directives or prose between tabs.

Required correction: analyst must specify exact token boundaries and ambiguous
input behavior; external review and focused fixtures must precede acceptance.
Fail closed on ambiguous ownership rather than attaching arbitrary siblings.

### AUDIT-007: file-wide automatic ops-ledger bypass weakens workflow coverage

`test_github_workflow.py:51-53` adds an autouse
`YDBDOC_SKIP_OPS_GATES=1` fixture for every test in the module. Test isolation
from external YDB is reasonable, but applying it implicitly to the whole suite
can hide lifecycle/quota/ledger wiring regressions. No authoritative spec
requires this test policy.

Required correction: use explicit fixtures only for tests unrelated to ops, and
retain at least one workflow integration test that exercises the real lifecycle
interface with an in-memory ledger rather than disabling it globally.

### AUDIT-008: TOC fixture path narrowing needs a retained root-path case

Workflow fixtures were changed from `ydb/docs/{ru,en}/a.md` to
`ydb/docs/{ru,en}/core/a.md` and an EN TOC was added. This makes TOC validation
realistic and is compatible with YDB docs, but it removes coverage for Markdown
directly below the locale root while v010 says every Markdown path under
`ydb/docs/ru/`. Preserve a root-level path test or explicitly constrain the
product scope in an approved specification.

### AUDIT-009: note/cut title and unknown-segment changes are in-scope but
under-documented

Adding `NOTE_TITLE` and `CUT_TITLE` segmentation/reinsertion is consistent with
“translate all prose” and fixes an otherwise unsupported segment kind. This is
not currently a blocker if focused round-trip tests cover both constructs and
unknown kinds fail closed. The implementation report should name this behavior
and its tests instead of presenting it as an incidental TypeError fix.

## Decisions found conformant or conditionally justified

- `chat_once` and immutable six-role model wiring appear intentionally separated
  from the existing shared `chat` behavior as required by their prerequisite
  specifications. Full external code review is still required.
- Pinning `base_commit_sha` in branch preparation is directly justified by the
  immutable publication-tree requirement.
- Replacing fake PR SHA values with real fixture commit SHAs is necessary for
  the provenance guard and does not weaken behavior.
- The pure RU-to-EN fragment mapping helper can remain only as a read-only
  validator dependency under AUDIT-005; it must never enter translation output
  mutation.
- Leaving unrelated repository-wide Ruff findings untouched is reasonable as a
  scope consideration, but the waiver itself was not authorized. It must be
  formally specified and reviewed.

## Acceptance gate

## Additional production-code blockers from independent call-path audit

Evidence classification and governing clauses:

| Finding | Evidence | Governing clause |
|---|---|---|
| AUDIT-011 | reachable call path and explicit raw-label fallback in source | LLM-WIRING “Exact production wiring”; ONE-PASS “Mandatory removal” |
| AUDIT-012 | deterministic control-flow defect | ONE-PASS “Fixed acquisition policy” |
| AUDIT-013 | direct set/dict duplicate collapse | ONE-PASS duplicate prose-ID rule |
| AUDIT-014 | direct serialized request body inspection | ONE-PASS “Structured critic contract” and “Minimal replacement protocol” |
| AUDIT-015 | independently reproduced runtime malformed image round-trip | ONE-PASS “Protection grammar” and “Atom round trip” |
| AUDIT-016 | direct callback/call-set inspection | ONE-PASS post-repair global invariants |
| AUDIT-017 | reproduced `CancelledError -> AcquisitionBlockedError` | CHAT-ONCE cancellation contract |
| AUDIT-018 | reproduced provider unavailable -> permanent error | CHAT-ONCE typed errors; LLM-WIRING acquisition table |
| AUDIT-019 | regex scanner source inspection and missing syntax tests | ONE-PASS “Queue and dependency closure” |
| AUDIT-020 | direct early-return/result-shape inspection | ONE-PASS provenance warning artifact |
| AUDIT-021 | direct config/default inventory | ONE-PASS “Mandatory removal” and “Migration plan” |

### AUDIT-011: production navigation escapes into the forbidden shared model chain

`github/workflow.py:715-727` reaches `run_navigation_merges`, whose label
translation in `pipeline/navigation_merge.py:240-277` calls shared
`client.chat(..., role="translate")`. That path retains dynamic model chains and
retries. Malformed JSON returns the original labels, including RU, as a
successful fallback. The static wiring test scans a hard-coded subset of files
and omits this real entrypoint path. This violates CHAT-ONCE, LLM-WIRING and the
ONE-PASS ban on hidden model selection and raw-RU fallback.

### AUDIT-012: protocol-invalid candidates are accepted before required gates

`translation/one_pass.py:238-262` lets acquisition accept a response after only
schema and ID-set parsing; token order, empty prose and Cyrillic checks run
after the controller has stopped. A damaged primary therefore blocks instead
of advancing immediately to the fixed fallback model. Local repair has the same
separation: schema validation is inside acquisition while atom/range checks are
outside, consuming the wrong logical attempt/model sequence.

Required correction: every protocol validity rule belongs inside the
AcquisitionController parser for that candidate. Rejected candidates must never
be accepted/rendered and must follow the exact transition table.

### AUDIT-013: duplicate prose IDs are silently accepted

`one_pass.py:36-44` compares sets and returns a dict. Duplicate IDs collapse, so
`[A, A, B]` can pass when expected is `{A, B}`. v010 explicitly classifies
duplicate IDs as protocol-invalid. Validate ordered cardinality and exact
one-occurrence-per-ID before building a mapping.

### AUDIT-014: local repair leaks the complete RU document and lacks atom manifest

`local_repair.py:297-299` serializes full `ru_text` into each repair request,
exposing protected code, configuration and directives. v010 permits only the RU
prose corresponding to the editable block. Critic input also omits the required
protected atom manifest, while `_canonical` accepts arbitrary `atom_ids` without
verifying membership. The existing “code not sent to repair” test has a clean
critic and never exercises a repair request.

### AUDIT-015: required atom round-trip is incomplete and images are broken

Image protection/restoration nests an image wrapper inside another image
wrapper. A source such as `![картинка](img.png =100x200 "title")` restores as a
nested malformed destination and then fails `lossless source slot not found`.
The atom matrix lacks images. Emphasis/strong delimiters are left as editable
literal Markdown rather than source-owned structure, and token validation
cannot detect their mutation.

Required correction: parser-owned image and inline-container atoms with exact
round-trip tests for image dimensions/title, nested emphasis, escaping and
links. No regex repair fallback.

### AUDIT-016: local repair global validation is materially weaker than v010

`one_pass.py:280-289` passes only `assert_no_protect_token` as
`global_validate`. A repair can introduce Markdown/YFM/container/link/anchor
damage and pass the repair controller. Transaction checks do not cover the full
atom/container structural parity matrix. Run all deterministic global
invariants after every candidate insertion and after final re-criticism.

### AUDIT-017: cancellation contract is broken

`translation/acquisition.py:101-119` catches `BaseException`, so
`asyncio.CancelledError` is wrapped as `AcquisitionBlockedError`. CHAT-ONCE
requires cancellation to propagate unchanged and prevent all subsequent calls.
Catch only classified ordinary exceptions and explicitly re-raise cancellation.

### AUDIT-018: Yandex model-unavailable classification is not implemented

`llm/client.py:175-186` does not normalize a provider model-not-found/unavailable
response to `LLMModelUnavailableError`; it can become permanent
`LLMRequestError`. Existing tests inject an already typed exception and therefore
do not test provider normalization. Add status/code-based provider tests and the
required immediate fallback transition.

### AUDIT-019: dependency closure uses regex instead of parser-recognized atoms

`pipeline/dependency_queue.py` scans `_LINK` regex matches, missing valid nested,
escaped, angle-bracket and reference-style Markdown links. This contradicts the
parser-recognized dependency contract. Duplicate unresolved occurrences are
also emitted independently instead of one canonical record with all source
locations.

### AUDIT-020: structured provenance report is discarded

`github/workflow.py:593-600` reduces rich provenance findings to
`completeness_gaps`, logs the details and returns. Users lose the mandated
reason, baseline/current OIDs and touching commits. Preserve the structured
warning artifact and surface it through the normal report without any model
call or staged output.

### AUDIT-021: legacy configuration deletion is incomplete

Incremental-era fields such as `segments_per_batch_chars`,
`critic_feedback_retries`, and RU translation skip-glob configuration remain in
`config/loader.py` and `config/default.yaml`; some remain active in navigation.
v010 requires removing corresponding fields, parsers and tests and forbids RU
Markdown bypass configuration. Separate truly navigation-only settings under a
non-translation namespace if still required, through analyst review.

## Acceptance gate

Do not hand this implementation to the tester as conformant until all RED
findings are corrected through analyst-authored amendments and configured
external review. After corrections, require:

1. exact same-file migration evidence and traceable moved tests;
2. no regex/post-restore href writer;
3. real GitHub workflow TOC-forwarding test;
4. complete preserved read-only coverage inventory;
5. no hidden skip/xfail/autouse gate bypass;
6. full pytest, approved Ruff scope, diff-check, import smoke and static
   forbidden-reachability checks;
7. a corrected implementation report listing every deviation and resolution.
