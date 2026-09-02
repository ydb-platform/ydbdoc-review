# TASK-51797-ONE-PASS-REMEDIATION v005

## Authority and objective

Repair every finding confirmed by external implementation audit
`implementation-audit-v002`. This specification is subordinate to and must
simultaneously satisfy CHAT-ONCE v003, LLM-WIRING v002, ONE-PASS v010 and
COVERAGE-DELETION v008. It does not authorize compatibility shims, resurrection
of legacy semantics, skipped tests, or developer-selected alternatives.

The current implementation is RED. No commit, tag, publication or production
translation is allowed until every phase and acceptance gate below is GREEN.

## Fixed remediation order

1. Restore test evidence and mark the implementation report NOT READY.
2. Fix CHAT-ONCE/acquisition failure semantics.
3. Fix atom protection and single restoration.
4. Fix translation, critic and local-repair protocol boundaries.
5. Replace dependency closure with parser-owned edges.
6. Move navigation translation to the translation-local model policy.
7. Preserve provenance and production TOC plumbing.
8. Remove legacy configuration and complete forbidden-reachability deletion.
9. Resolve separately reviewed parser/test-policy gaps.
10. Run the complete gate and write a truthful implementation report.

No later phase may be used to hide a failure in an earlier phase.

## Finding-by-finding requirements

### R-001: restore v008 same-file migration evidence

Root cause: blocker suites and regression fixtures were deleted directly instead
of first being rewritten and run GREEN in place.

Contract: COVERAGE-DELETION v008, same-file migration and delete-after-GREEN
sections.

Changes:

- restore from base the exact deleted blocker files and harness case fixtures;
- do not restore production harness/writers;
- rewrite each restored test in its original file against v010 APIs exactly as
  mapped by v008;
- preserve original test name or original path/name as a parametrized case ID;
- run every restored file GREEN;
- only then move the approved body unchanged, apart from imports, to its named
  destination and delete the empty legacy test file/fixture;
- retain `test_fence_comments.py` as the mixed read-only file required by v008;
  do not replace it solely with a differently named file.

Acceptance: a machine-readable migration manifest lists legacy path/test,
same-file GREEN command/result, destination path/test and final status. Git diff
must prove the body was not weakened during the move.

### R-002: remove forbidden regex href postmutation

Root cause: `_restore_source_hrefs` performs positional regex rewriting after
parser-owned restoration.

Contract: ONE-PASS “Protection grammar”, single render/restoration and only-
writer invariants; v008 fragment retirement.

Changes:

- delete `_restore_source_hrefs`, `_HREF` when unused, and its call;
- represent complete link wrappers/destination/title/escaping as parser-owned
  atoms with translated label prose slots;
- perform `/ru/` to `/en/` locale mapping exactly once in the atom restoration
  layer, only for the defined absolute locale-path form;
- never pair links positionally or normalize their bytes through regex.

Tests: duplicate/reordered/nested labels, escaped/angle destinations, query,
fragment, title, image-link nesting, autotitle, absolute `/ru/`, relative link,
and repeated identical link atoms. Assert byte-exact wrapper restoration and no
post-restore writer symbol/import.

### R-003: real GitHub workflow TOC forwarding

Root cause: existing test injects a pre-built sentinel into `run_pr_translation`
and bypasses the production builder/workflow edge.

Contract: COVERAGE-DELETION v008 Section E item 13.

Changes/tests: in the production GitHub workflow test, make
`build_en_toc_reachable_from_repo` return one unique sentinel object, invoke
`run_doc_translate`, and spy all applicable read-only href/link validators.
Assert the identical object reaches each validator through workflow -> v010
transaction. Do not mock any intermediate forwarding edge. Keep a separate
transaction-level forwarding unit test if useful.

### R-004: truthful implementation report and Ruff gate

Root cause: report claimed readiness and approved deletion ordering that had not
occurred, and invented a focused-Ruff waiver.

Contract: v008 completion gate and implementation audit v002.

Changes: immediately change report status to NOT READY and enumerate open IDs.
At completion list exact commands/results and deletion manifest. Repository-wide
Ruff is required by current contract. If the 313 findings are pre-existing,
produce a separate analyst amendment defining a baseline-aware command that
fails on every newly introduced finding; obtain external APPROVED before using
it. Developer cannot declare the waiver.

### R-005: complete traceable fragment retirement

Root cause: most old fragment test identities disappeared; a private pure mapper
was retained without explicit API ownership.

Contract: v008 fragment same-file contract.

Changes:

- restore every legacy fragment test identity as same-file parametrized case;
- map it to exact RU href preservation, staged Cyrillic-anchor rewrite, or
  blocking out-of-scope validation;
- retain `fragment_declared_in_markdown` as read-only;
- rename `_remap_fragment_via_ru_en_pages` to a public read-only validator helper
  `map_ru_fragment_to_declared_en_fragment` in `href_parity.py`; move its tests
  with it; `redirect_impacts` may import this public helper;
- the helper must return comparison metadata only and must never be reachable
  from translate/render/staging mutation.

Acceptance: static call graph permits callers only from read-only href/redirect
validators; all legacy case IDs present; no baseline/sibling href writer.

### R-006: specify and constrain YFM-tabs parsing

Root cause: arbitrary sibling blocks are attached to the previous tab.

Contract: ONE-PASS lossless YFM structure and fail-closed behavior.

Decision: unindented siblings may be attached only when token source maps show
they occur between a recognized tab list item body and the next list item/end
marker without an intervening block-level YFM directive. Otherwise parsing
raises a typed ambiguous-YFM error before model use. Leading content is never
silently assigned to the first tab.

Tests: real approved fixtures, multiple tabs, directive boundaries, leading
content, trailing content, nested containers and ambiguous input. Assert
parse/render idempotence or typed pre-model block.

The parser must not infer ownership from already-built sibling order.
`yfm_plugins/tabs.py` slices containers before recursive tokenization. Every
matched tabs container receives a monotonically unique `container_id`, its
`parent_container_id`, opening/closing `SourceSpan`, and opening indentation. At
that container's direct depth, a Markdown bullet marker at the container opening
indent starts a tab. The plugin emits `yfm_tab_open` containing the title span,
complete tab span and container ID, tokenizes only that tab body slice, then
emits `yfm_tab_close`. Nested tabs are recursively claimed by their child
container and appear as one body block; their bullets never start outer tabs.
Fences, nested lists and other YFM containers are similarly consumed before tab
header detection.

`_parse_yfm_tabs` accepts only paired `yfm_tab_open`/`yfm_tab_close` for the
current container. Every token between a pair belongs to that tab, including
blocks Markdown-it formerly exposed as leading siblings. Invalid orphan means
nonblank, non-comment bytes at current-container direct depth before the first
tab or between closed tab ranges with no owning open tab. It raises
`AmbiguousYfmStructureError` before segmentation/model calls. Blank lines and
comments remain byte-preserved trivia; empty tabs are valid. Synthetic
empty-title tabs, append-to-previous heuristics and discarded blocks are
forbidden.

Rendering preserves variant, ordered title/body nodes and container trivia.
Parse-render-parse reproduces identical ownership and byte-equivalent Markdown
modulo the already-approved newline normalization. Tests run the complete real
`tests/fixtures/markdown_files/ru/core/reference/ydb-sdk/topic.md`, every nested
tabs occurrence, leading-sibling regression, nested fence/list/YFM cases, true
direct-depth orphan, empty tab, comments/blanks, and block-count/content hashes
proving zero loss or duplication.

### R-007: remove module-wide ops-ledger bypass

Root cause: autouse `YDBDOC_SKIP_OPS_GATES=1` disables lifecycle behavior for all
GitHub workflow tests.

Contract: preservation of non-translation GitHub/ops behavior in v008.

Changes: remove autouse fixture. Tests unrelated to ops may explicitly request a
local isolation fixture. Add at least one `run_doc_translate` workflow test with
`InMemoryRunsLedger` exercising start, cost/final status and failure finalization.
No network/YDB dependency is allowed in unit tests.

### R-008: restore locale-root Markdown coverage

Root cause: all workflow fixtures moved from locale root to `/core/`.

Contract: ONE-PASS initial queue includes every added/modified Markdown under
`ydb/docs/ru/`.

Changes/tests: keep realistic `/core/`+TOC fixtures, and add an explicit
`ydb/docs/ru/root-page.md` case asserting one-pass queueing, EN counterpart path,
provenance guard and atomic publication. No `/core/` hard-coded production rule.

### R-009: document and close note/cut/unknown segment behavior

Root cause: note/cut titles were added incidentally.

Contract: ONE-PASS translates every prose slot and blocks unknown structure.

Decision: `NOTE_TITLE` and `CUT_TITLE` are prose slots. Protect their inline
atoms, translate in the single file payload, reinsert once, and test exact
round-trip. Any unknown `SegmentKind` raises a typed pre-render error and stages
nothing. Record the behavior in the final report.

### R-010: restore mixed placeholder and table-cell coverage

Root cause: `test_placeholder_repair.py` was deleted wholesale and a table-cell
critic test was removed without equivalent coverage.

Contract: v008 mixed detection/blocking preservation; ONE-PASS atom matrix.

Changes: restore placeholder detector/blocking tests in their original file;
delete only writer expectations after same-file GREEN equivalents. Add a
traceable table-cell critic/local-repair test proving token/structure parity and
rollback on corrupt candidate.

### R-011: navigation uses translation-local bounded acquisition

Root cause: production navigation calls shared `client.chat` and returns raw RU
labels on malformed JSON.

Contract: LLM-WIRING “Exact production wiring”; ONE-PASS ban on dynamic chains,
hidden retries and raw-RU fallback.

Decision: navigation label translation uses a dedicated immutable pair in the
same translation job manifest. Because the approved six slots are closed, it
must use the `translate` pair and `TranslationChatOnce` with a distinct role
metadata value that does not introduce a seventh model slug. Candidate
acquisition follows the same fixed two-model/four-request classifier. Invalid or
exhausted navigation translation blocks the whole transaction; raw RU labels
are never success. Navigation deterministic merge remains separate.

Tests: real `run_doc_translate` navigation path, shared `chat` raising spy,
exact model slugs/attempts, malformed primary -> fallback, exhaustion -> zero
publication, no RU fallback.

### R-012: all candidate protocol gates run before acceptance

Root cause: schema/ID parsing is inside acquisition, but token, empty, Cyrillic,
range and atom gates run after acceptance.

Contract: ONE-PASS fixed acquisition table.

Changes: candidate parser for translation performs schema, exact ordered
one-occurrence ID parity, token grammar/order/nesting, non-empty prose and
prose-Cyrillic gates before returning payload. Repair candidate parser performs
finding/block identity, atom immutability, allowed UTF-8 range, replacement
scope and global pre-insert structural validation before acceptance. A rejected
candidate advances exactly according to the protocol-invalid row.

Tests: one case per gate with invalid primary and valid fallback; assert rejected
payload never renders/inserts and exact attempt records.

### R-013: reject duplicate IDs

Root cause: set comparison and dict construction collapse duplicates.

Contract: ONE-PASS duplicate-ID protocol violation.

Changes: compare list cardinality plus a frequency map; require every expected
ID exactly once and no extras. Test `[A,A,B]`, missing, extra and reordered IDs
according to the specification’s ordering rule. Duplicate primary must advance
immediately to fallback.

### R-014: minimal repair and critic context

Root cause: repair request contains full RU document; critic lacks verified atom
manifest.

Contract: ONE-PASS structured critic and minimal replacement protocol.

Changes:

- construct stable block records during base render containing EN editable
  prose, corresponding RU prose only, allowed range and atom IDs/hashes;
- critic receives rendered document, block records and protected atom manifest,
  but not raw protected bytes unnecessarily;
- repair receives only one block, corresponding RU prose, exact finding/range,
  applicable glossary/context and immutable atom IDs/hashes;
- verify returned atom IDs against the manifest.

Test with unique secrets in code/config/directives and trigger actual repair;
inspect all messages and assert secrets never occur.

### R-015: complete image and inline-container atom protection

Root cause: image wrapper is nested during restoration; emphasis delimiters are
editable syntax.

Contract: ONE-PASS protection grammar and atom matrix.

Changes: represent each image as one opaque source-owned node while exposing
only explicitly allowed alt prose as a nested slot, never as a wrapper marker
inside another wrapper. Strong/emphasis/strike/link nesting are structural nodes
with paired opaque boundaries and validated nesting. Restore nodes once.

Tests: image alt, empty alt, dimensions, title, escaped URL, linked image,
emphasis/strong/strike nesting and deliberate marker crossing/loss. Assert
byte-exact non-prose atoms and one render.

Strike representation is closed as a new `InlineStrike` AST node in
`parsing/ast_types.py`: discriminator `kind: Literal["strike"]`, recursively
typed `children: list[InlineNode]`, and sole marker
`Literal["~~"] = "~~"`. Add it to `InlineNode` and `model_rebuild()`.
`markdown_parser.py` converts every matched `s_open`/`s_close` pair to this node
and never drops its children. `markdown_renderer.py` renders exactly `~~` plus
recursively rendered children plus `~~`. `inline_protector.py` treats both `~~`
delimiters as source-owned structural boundaries while recursively exposing only
translatable child prose; restore/reinsert rejects missing, duplicated,
reordered or crossing boundaries. Plain-text emulation and an opaque whole
container are forbidden. Parser round-trip, renderer coverage, segmentation and
atom round-trip tests cover plain strike, nested emphasis/strong/link/code, and
malformed boundaries.

### R-016: full global validation after every repair

Root cause: repair controller receives only protect-token leak validation.

Contract: ONE-PASS local repair global invariants.

Changes: define one deterministic `validate_complete_document` used after base
render, before accepting each repair insertion, after insertion/re-critic and
before staging. It checks token absence, parser success, Markdown/YFM container
parity, source-owned atom hashes/order, link/href/fragment/anchor rules, fence/
config equality and residual Cyrillic classification. It is read-only.

Tests: each invariant independently corrupted by a repair candidate; assert
candidate rejection, correct attempt accounting and atomic rollback.

### R-017: propagate cancellation unchanged

Root cause: acquisition catches `BaseException` and wraps cancellation.

Contract: CHAT-ONCE cancellation section.

Changes: never catch `BaseException`; explicitly re-raise `asyncio.CancelledError`,
`KeyboardInterrupt` and `SystemExit`. No usage/error replacement or next-model
call after cancellation.

Tests: cancellation before dispatch and during primary dispatch for translate,
critic, repair and navigation; assert same exception object and one-or-zero calls.

### R-018: typed provider model-unavailable normalization

Root cause: Yandex raw provider unavailable/not-found responses become permanent
generic errors; tests inject already-normalized exceptions.

Contract: CHAT-ONCE typed errors and LLM-WIRING transition table.

Changes: normalize using provider HTTP/status/error code fields. Map only
documented model-not-found/unavailable codes to `LLMModelUnavailableError`; do
not rely solely on message substrings. Preserve the approved inheritance/MRO.

Tests: raw provider response/exception fixtures for unavailable, auth, rate
limit, server, malformed protocol and unknown. Assert exact type/kind and
immediate fallback only for unavailable/protocol rows.

Classification priority and table are closed. Apply in order: propagate
`CancelledError`, `KeyboardInterrupt`, `SystemExit`; preserve an existing typed
`LLMRequestError`; read structured provider `error.code` (or OpenAI exception
`code`) and HTTP status; use the narrow legacy-message row last.

| Provider evidence | Typed result | Same-model retry | Next frozen model | Job outcome |
|---|---|---:|---:|---|
| code exactly `MODEL_NOT_FOUND` or `MODEL_UNAVAILABLE`, HTTP absent/400/404 | `LLMModelUnavailableError` | no | yes | block if fallback unavailable |
| HTTP 408/429/500/502/503/504, no model code | `LLMRetryableRequestError` | yes, approved bound | after exhaustion | block after chain exhaustion |
| HTTP 401/403 | `LLMRequestError` permanent | no | no | block immediately |
| other known 4xx, including 400/404 without model evidence | `LLMRequestError` permanent | no | no | block immediately |
| completed 2xx with missing/invalid completion | `LLMProtocolResponseError` | no | yes | block if fallback invalid |
| no structured code and HTTP absent/400/404; normalized message matches case-insensitive anchored `^failed to get model(?:\\s*[:.]|$)` | `LLMModelUnavailableError` | no | yes | block if fallback unavailable |
| unknown code, unknown/missing HTTP, or conflicting evidence | `LLMRequestError` permanent | no | no | block immediately |

Priority within evidence: 401/403 always wins; 429/5xx wins over message text;
a recognized model code wins only with absent/400/404 HTTP; then the narrow
legacy message row applies. Every other phrase or substring is forbidden.
Table-driven tests assert class, `chat_once_kind`, dispatch count, sleep count,
fallback slug and terminal result for all rows, plus 401 + model code, 429 +
legacy message, unknown code + 404, and exact/non-exact message boundaries.

### R-019: parser-based dependency closure

Root cause: regex link scanning misses valid Markdown and duplicates unresolved
records.

Contract: ONE-PASS queue and dependency closure.

Changes: parse the authoritative RU bytes once; traverse InlineLink and direct
YFM include AST nodes; resolve canonical target identity while retaining every
source occurrence/location. One unresolved record per canonical target/reason
contains all referring locations. Remove `_LINK` regex dependency discovery.

Tests: nested label, escaped and angle destination, reference-style link if the
parser supports it, images excluded, code/fence examples excluded, duplicate
fragments aggregated, link+include shared node/budget, cycles and 20/21 bound.

Source coordinates are closed as `SourceSpan` in `parsing/ast_types.py` with
`byte_start` and `byte_end` as zero-based UTF-8 byte offsets in a half-open
range, plus `line` and `column` as one-based coordinates of the first Unicode
code point. `InlineLink` and `YfmInclude` each gain required `source_span`.
`UnresolvedDependency` contains `occurrences: tuple[DependencyOccurrence, ...]`;
each occurrence has source document path, edge kind (`link` or `include`), raw
destination and the exact `SourceSpan` of the complete Markdown link/include
syntax.

`markdown_parser.parse_markdown(source)` owns population. Its Markdown-it
adapter carries token start/end cursor positions into AST construction; it
converts character cursors to UTF-8 offsets from a precomputed line-start table.
Dependency code may only traverse these AST fields. It must not rescan source,
search substrings, or use regular expressions. Occurrences preserve source order
and are deduplicated only by `(source_path, edge_kind, byte_start, byte_end)`.
Canonical targets are deduplicated separately; all distinct occurrences are
aggregated on that target and sorted by the same tuple. Pydantic serialization
includes spans deterministically; rendering ignores spans so Markdown bytes are
unchanged. Tests include repeated identical links on one line, multibyte text
before a link, nested label, links on different lines, duplicate link/include
target, and JSON model dump/load, asserting exact offsets and 1-based line/
column values.

### R-020: preserve structured provenance findings

Root cause: early workflow return reduces rich findings to completeness paths.

Contract: ONE-PASS publication provenance guard and warning artifact.

Changes: add provenance findings to the typed PR/job result and normal report
builder. Preserve reason, RU/EN paths, baseline/current OIDs or absence and every
touching commit. The early block makes zero model calls and stages nothing, but
still posts/returns the structured report through the authorized reporting path.

Tests: each reason (`history_diverged`, `newer_ru`, `newer_en`, `en_created`,
`en_deleted`, `source_pr_en_conflict`) through `run_doc_translate`, asserting
report fields, zero calls and zero publication.

### R-021: remove legacy configuration and skip controls

Root cause: incremental batch/retry and translation skip fields remain.

Contract: ONE-PASS mandatory removal/migration plan.

Changes: remove `segments_per_batch_chars`, `critic_feedback_retries`, old model
chain/fallback translation settings and RU Markdown skip-glob parsing/defaults/
tests. If navigation needs an exclusion, define a new navigation-only field that
cannot match `.md` under the RU docs root. Unknown removed config keys fail
strict validation rather than being ignored.

Tests: removed keys rejected; every added/modified RU Markdown including prior
skip patterns queues one-pass; navigation-only exclusion cannot affect docs.

R-021 owns all four remaining `github/workflow.py::run_doc_verify` call-site
dispositions. The two `filter_translate_changes(..., translate_skip_globs)`
assignments are deleted: `changes` and `source_changes` retain the complete API/
Git union. The conditional `TranslationScopePlan` rebuild through
`filter_path_set` is deleted without replacement. The
`run_navigation_verifies(..., skip_globs=...)` argument is deleted together with
that parameter and all `_drop_skipped_from_toc_scope`/`toc_entry_is_skipped`
translation-skip paths in `pipeline/navigation_merge.py`.

Translate behavior: every added, modified, renamed or deleted RU Markdown path
under docs root enters provenance, dependency planning and one-pass transaction;
no config/default can filter it. Read-only verify behavior: every changed RU or
EN Markdown pair enters validators, including paths formerly matched by defaults.
The optional `navigation_only_exclusions` is accepted only by navigation pair
discovery and only for non-Markdown navigation files (`toc.yaml` and explicitly
typed navigation assets); loader rejects patterns/values that match `.md` or a
path under `docs_root/{ru,en}`. It is never passed to document change filtering,
scope planning or validator target collection. There is no empty-tuple/default
alias retaining the old skip semantics. Tests exercise all four former workflow
call sites and navigation-merge callers with the former default patterns,
asserting complete pair/scope/validator membership and static absence of
`translate_skip_globs`, `filter_translate_changes`, `filter_path_set` and
`skip_globs` on the production translation/verify reachability path.

## Dependency and deletion rules

- R-001/R-010 test restoration precedes production deletion.
- R-017/R-018 precede acquisition refactoring R-012/R-013.
- R-015 and R-002 precede R-014/R-016 so repair uses stable atom records.
- R-019 precedes final queue/transaction integration.
- R-011 and R-020 precede real workflow test R-003.
- R-021 and all reachability tests precede the final legacy deletion scan.
- No old writer, harness, shared-chat translation route or raw-RU fallback may be
  temporarily reintroduced. Use failing replacement tests first.

## Final acceptance matrix

Required GREEN groups:

1. same-file migration manifest and restored/moved regression suites;
2. CHAT-ONCE provider/cancellation tests;
3. translation/navigation acquisition transition matrix;
4. complete atom round-trip matrix;
5. critic/minimal repair/context secrecy/global validation matrix;
6. parser dependency BFS/budget/unresolved aggregation matrix;
7. provenance and real workflow TOC/navigation tests;
8. mixed read-only validator, GitHub, reporting, ops and navigation suites;
9. static AST/import/call closure from every CLI production entrypoint;
10. full pytest, approved Ruff command, diff-check, compile/import smoke.

The final static closure must enumerate entrypoints and traverse imports/calls;
a hard-coded allowlist of six files is insufficient. It must prove:

- translation/navigation model calls use only `TranslationChatOnce` and the
  frozen manifest;
- shared `chat`, dynamic model chains and raw-RU success fallbacks are
  unreachable from translation jobs;
- legacy harness/differential/critic-only/repair writers/config keys are absent;
- document writes occur only through validated transaction staging/publication.

Final report status may become READY only after an independent tester report and
configured external implementation review both approve the corrected diff.

## Executor control and machine-enforced scope

`implementation-plan.yaml` is normative and has equal authority to this
specification. Developer work is permitted only inside its union of
`allowed_files`, on its `allowed_symbols`, and for its declared operation.

Before implementation, add a policy gate which loads the plan and fails RED if:

1. any changed path is outside the union allowlist;
2. a deleted file, test, fixture or public symbol is absent from the exact
   `allowed_deletions` of its requirement;
3. a new production symbol is absent from `allowed_symbols` with operation
   `add` or `replace`;
4. any changed production/test file lacks at least one requirement ID in the
   implementation manifest/report;
5. specification, review response, request or workflow-protocol files are
   changed by the developer.

The developer may not spawn or delegate to subagents. If an item is ambiguous,
write `.ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/questions/<requirement_id>.md`
with exact question/evidence, stop only that item, and continue independent
items. Only the analyst may amend the plan after external review.

After implementation, the analyst performs a complete diff-to-plan conformance
audit before tester handoff. The tester runs policy gates before any functional
tests; a policy RED stops functional testing. Neither passing tests nor a report
can waive a plan violation.

## Closed implementation-manifest contract (v003 amendment)

The sole implementation manifest path is
`.ai-workflow/tasks/TASK-51797-ONE-PASS/implementation-manifest-remediation-v003.yaml`.
It is a developer-owned implementation report artifact, not a specification,
review request, reviewer response, queue file, heartbeat or workflow-protocol
file. The developer may create and update only this named artifact under
`.ai-workflow`; all other protocol/specification immutability remains absolute.
The manifest is inside the executor allowlist but outside the changed-code
allowlist: changing it never authorizes a source, test or configuration change.

The manifest covers the entire final worktree delta against base commit
`9ff8edec9a26d3975306e20adca325c6eb9f77e6`, not merely remediation edits. At
executor start the observed inventory is 85 tracked paths plus 29 untracked
paths. These counts are an input sanity check, not a frozen final count: every
later added, modified, renamed or deleted path also requires exactly one entry.

Schema is fixed by `implementation-plan.yaml`. Each entry records path, Git
status, ownership class, immutable pre-remediation state, final state, and
governing requirement IDs. Every production, test, configuration, script and
report path maps to authoritative contract requirement IDs; remediation changes
also include the corresponding R-001..R-021 ID. Pre-existing untracked
`.ai-workflow` task artifacts are classified `control_artifact`, record their
SHA-256 baseline/final digest and authoritative task/version, and remain
byte-identical. Only this manifest and the already allowed implementation report
are mutable in the control tree. Reviewer responses and analyst specs, requests
and plans are always immutable.

The normative validation command is:

`python -m scripts.remediation_policy_gate validate --plan .ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/implementation-plan.yaml --snapshot .ai-workflow/tasks/TASK-51797-ONE-PASS/baseline-snapshot-remediation-v005.yaml --manifest .ai-workflow/tasks/TASK-51797-ONE-PASS/implementation-manifest-remediation-v003.yaml --base 9ff8edec9a26d3975306e20adca325c6eb9f77e6`

It enumerates tracked and untracked paths itself; rejects missing, duplicate or
stale entries; verifies hashes, statuses and ownership; enforces code/test
allowlists plus exact symbol/deletion declarations; and rejects mutation of
protected control artifacts. RED stops executor and tester. The analyst reruns
the same command before diff-conformance review, and tester runs it before all
functional tests.

## Closed whole-diff baseline and policy-gate bootstrap (v005)

The whole-diff rule has two disjoint allowlists. `baseline_contract_inventory`
permits an explicitly named pre-existing path/state to remain in the final diff
because it maps to an approved CHAT-ONCE, LLM-WIRING, ONE-PASS or
COVERAGE-DELETION requirement. It does not permit remediation edits.
`items[].allowed_files` is the sole edit allowlist. A path in both may change
only for the named remediation item and symbols. A baseline-only path whose
captured state changes is RED.

The question-time inventory was 85 tracked and 29 untracked paths. Later
publication of approved provenance and the permitted R-004 question increased
the current untracked count; those control files are explicitly inventoried,
not silently grandfathered. `uv.lock` has no approved requirement and must be
absent before implementation proceeds.

R-004 owns `scripts/remediation_policy_gate.py` and
`tests/unit/test_remediation_policy_gate.py`. Developer implements only `main`,
`load_plan`, `capture_baseline`, `load_manifest`, `enumerate_delta`,
`validate_path_inventory`, `validate_edit_allowlist`,
`validate_control_hashes`, `validate_requirement_mapping`,
`validate_symbol_changes`, and `validate_deletions`. It uses only the standard
library plus declared PyYAML and imports no production translation code.

Bootstrap order is exact: (1) implement only the R-004 gate and its tests;
(2) remove the unauthorized untracked `uv.lock`; (3) run the gate unit tests;
(4) run exactly the following capture command; (5) make no further change to
the snapshot. No other production/test edit is permitted before capture.

`python -m scripts.remediation_policy_gate capture --plan .ai-workflow/tasks/TASK-51797-ONE-PASS-REMEDIATION/implementation-plan.yaml --base 9ff8edec9a26d3975306e20adca325c6eb9f77e6 --output .ai-workflow/tasks/TASK-51797-ONE-PASS/baseline-snapshot-remediation-v005.yaml`

Capture is deterministic, refuses overwrite, records every tracked/untracked
path, status and SHA-256 (or base blob OID for deletion), and succeeds only when
every path/state is named by the plan. The snapshot is a developer-report
artifact outside the code allowlist and becomes immutable immediately. Validate
mode additionally receives `--snapshot` with that path. It permits unchanged
baseline-only entries, validates edits only against item allowlists/symbols,
demands exact final manifest coverage, and rejects new unplanned paths.
Tests cover modified/deleted/untracked baseline entries, R-item overlap,
post-capture mutation, missing/extra/duplicate entries, overwrite refusal,
control mutation and unauthorized new files. Analyst and tester use validate
mode only.
