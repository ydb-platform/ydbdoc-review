---
type: qa-ledger
date: 2026-08-28
project: ydbdoc-review-ng
status: authoritative
tags: [ng-failures, independent-qa, root-cause-analysis, acceptance-harness]
---

# Memory Bank: authoritative NG failure ledger

> Part of the [Memory Bank index](../../MEMORY_BANK.md). This is the authoritative
> evidence source for the 26 clean-restart finding modules required by §25.4.5.
> It is reconstructed from the independent QA verdicts and two independent RCA
> reviews, not from the deleted implementations or their tests.

## 26. Status and use

Every finding below is an **OPEN CLEAN-RESTART ACCEPTANCE OBLIGATION**, including
findings that the second implementation closed. `CLOSED` in closure history means
only that one later implementation passed the independent observation at that
time. The third implementation did not establish independent per-finding closure,
and all failed NG product/test code was deleted. A new acceptance harness must
therefore reproduce the bad behavior against its negative-control mutant and fail
before any new product implementation exists.

The fields are normative:

- **Original failure** preserves the independent tester's failure statement.
- **Contract mapping** identifies the product/specification/acceptance rule, but
  the referenced prose is not executable evidence.
- **Minimal reproduction** is the smallest black-box or real-boundary input.
- **Observed bad output** is the failed implementation observation.
- **Required good observation** is what an independent harness must see.
- **Negative-control mutant** is a deliberately wrong target that must make this
  finding fail for its own semantic reason.
- **Provenance and closure** separates first QA, first RCA, second QA and later
  evidence. An implementation-authored test name is never provenance.

## 26.1 Findings 01–26

### Finding 01: acceptance mapping asserted names, not behavior

- **Severity:** Critical, acceptance-system integrity.
- **Original failure:** the AC ledger mapped 176/242 collected nodes only by AC
  ID and file; 59 generic `test_*` nodes and production-adapter behavior had no
  meaningful coverage. Observable text was loaded from §24 itself, and mappings
  such as AC-166, AC-170, AC-171, AC-174 and AC-175 did not prove their stated
  predicates.
- **Contract mapping:** §24.18, §24.19; NG-AC-001..175, specifically 166,
  170–171 and 174–175; §25.4.2 and §25.4.5.
- **Minimal reproduction/input:** collect the claimed acceptance suite, then for
  each mapped AC inspect the executed external observation. Replace the target
  with a stub that returns the expected DTO/test-owned words but performs no TTL,
  recovery, cutover or audit behavior.
- **Observed bad output:** the ledger remained green with only 61 unique pytest
  nodes for 175 criteria; AC prose compared equal to prose parsed from the same
  specification and false mappings were accepted.
- **Required good observation:** every mapped criterion is backed by a frozen,
  test-owned input and externally observable predicate; high-risk criteria have
  real-boundary/fresh-process evidence and fail when their behavior is removed.
- **Negative-control mutant:** keep AC IDs, filenames and copied observable text,
  but replace the target behavior with unconditional PASS. Finding 01 must fail
  and name the missing observation, not accept the labels.
- **Provenance and closure:** first QA CONFIRMED; first RCA reproduced the
  175-to-61 collapse and false mappings; second QA OPEN; both RCA analysts
  confirmed it systemic. Attempt 3's in-tree ledger was rejected as
  implementation-coupled. Never authoritatively closed.

### Finding 02: production entrypoint retained a legacy escape

- **Severity:** Critical, production authority boundary.
- **Original failure:** the CLI dynamically imported legacy workflow code, and
  the deployed dry-run/no-commit entry path could route there; the static boundary
  test did not inspect dynamic/runtime import closure.
- **Contract mapping:** §23.17; §24.2–§24.2.2; NG-AC-001–007 and 174; §25.2 and
  §25.3.1.
- **Minimal reproduction/input:** start the exact production container command
  used by the action, invoke each command mode, and record all imported modules
  before any handler runs.
- **Observed bad output:** importing the top-level executable eagerly loaded 53
  legacy modules including pipeline, translation, parsing, segmentation, LLM and
  config; string scans of four files still passed.
- **Required good observation:** the release image contains and imports only the
  new distribution closure; no `ydbdoc_review` policy module is importable or
  loaded in any production mode.
- **Negative-control mutant:** add a dynamic import or debug/no-commit branch that
  loads one forbidden legacy module. The release-image test must fail even when
  ordinary AST imports remain clean.
- **Provenance and closure:** first QA CONFIRMED; first RCA confirmed the legacy
  production closure; second QA PARTIAL because label routing improved while the
  deployed import closure remained unproved. RCA A/B retained PARTIAL. Not
  authoritatively closed.

### Finding 03: delivery claim depended on mutable GitHub state

- **Severity:** Critical, event idempotency and duplicate effects.
- **Original failure:** `load_command_event` resolved the active label before the
  delivery claim, synthesized a timeline-based delivery ID, and a duplicate could
  fail before loading its saved result.
- **Contract mapping:** §23.1 command labels and concurrency; §24.5.1–§24.5.2,
  §24.12.3–§24.12.4; NG-AC-009–012, 027 and 165; §25.3.3.
- **Minimal reproduction/input:** preclaim and bind a run at RECEIVED, keep the
  command label active, then deliver the identical webhook again after changing
  current timeline/label state.
- **Observed bad output:** the duplicate returned PASS, left the label active,
  called no label removal, and still pushed a branch, created a Draft, updated
  metadata and comments. Duplicate behavior depended on `created`, not persisted
  phase.
- **Required good observation:** immutable webhook delivery ID is claimed before
  GitHub reads; duplicate loads the same run and resumes exactly the first
  incomplete phase, without duplicate paid or GitHub effects and without touching
  a newer label application.
- **Negative-control mutant:** resume a preclaimed run through the normal new-run
  path or key it by the latest timeline event. The duplicate/newer-label scenario
  must fail.
- **Provenance and closure:** first QA CONFIRMED; first RCA reproduced the PASS
  plus active label and duplicate mutations; second QA OPEN; RCA A/B agreed the
  fix requires one durable phase dispatcher. Never closed.

### Finding 04: fallible bootstrap escaped label removal and reporting

- **Severity:** Critical, command lifecycle and user safety.
- **Original failure:** config, secrets, adapters and schema were constructed
  before the router claim/terminal boundary, so missing config could leave the
  label and acknowledgement/report unfinished.
- **Contract mapping:** §23.1, §23.13–§23.14; §24.5.2, §24.5.4–§24.5.5 and
  §24.14; NG-AC-011–022 and 165; §25.3.3.
- **Minimal reproduction/input:** use a valid labeled webhook and persistence
  claim, then make `NgConfig`, S3, provider or schema construction fail one at a
  time after claim.
- **Observed bad output:** with missing config, the run remained RECEIVED with
  bound timeline event, null result, active label and no mutation/report.
- **Required good observation:** a minimal control plane claims and removes the
  one-shot label; all later dependency factories execute inside a persisted phase
  whose failure writes one clear Russian terminal report and result.
- **Negative-control mutant:** eagerly instantiate one required provider before
  entering the router/phase failure boundary. Its injected constructor failure
  must fail this finding.
- **Provenance and closure:** first QA CONFIRMED; first RCA reproduced the stranded
  RECEIVED row; second QA OPEN; RCA A/B classified it as a control-plane root
  cause. Never closed.

### Finding 05: a paid response could have no durable cost/result row

- **Severity:** Critical, billing safety and crash recovery.
- **Original failure:** model artifacts were written before
  `persist_model_attempt`; a paid provider response could exist with zero cost
  row after process loss.
- **Contract mapping:** §23.14; §24 v1.0.3 vocabulary, §24.9.2, §24.12.1,
  §24.12.3–§24.12.4; NG-AC-020–022, 104, 169, 171 and 175; §25.3.5.
- **Minimal reproduction/input:** an external provider spy accepts one stable
  call ID; kill the worker process after the provider returns but before the YDB
  result transaction, then restart against the same real store.
- **Observed bad output:** provider call count was 1, cost rows 0, reservation
  remained, and resume raised `MODEL_CALL_RESPONSE_PENDING_RECONCILIATION`.
- **Required good observation:** RESERVED is durable before dispatch; the call ID
  is sent at most once; ambiguous restart becomes UNKNOWN_BILLED, blocks later
  paid work that Moscow day but not zero-call work, invents no cost, and clears
  only from exact authoritative provider evidence.
- **Negative-control mutant:** resend RESERVED on restart, or convert missing
  usage to zero/free. The external spy and persisted-state assertions must fail.
- **Provenance and closure:** first QA CONFIRMED; first RCA reproduced the crash
  window; second QA PARTIAL because ordering improved but the provider API had no
  idempotency/result lookup. RCA A/B agreed strict response atomicity was
  impossible and produced the §24 v1.0.3 ruling. The implementable obligation is
  still open for clean-restart acceptance.

### Finding 06: external mutation intent was recorded after the effect

- **Severity:** Critical, orphan Draft/branch and duplicate mutation risk.
- **Original failure:** `DRAFT_CREATE_INTENT` was persisted after publication,
  only the latest journal phase was retained, and retry could freeze or duplicate
  an orphan branch/Draft.
- **Contract mapping:** §23.1 clean restart, §23.10 atomic publication;
  §24.12.3–§24.12.4 and §24.13; NG-AC-155–165 and 169–171; §25.3.4.
- **Minimal reproduction/input:** persist intent, let an external GitHub spy
  create Draft `pr-1`, kill before REMOTE_CONFIRMED, then restart in a new process.
- **Observed bad output:** the generic checkpoint executed the remote effect
  twice (`pr-1`, `pr-2`) and journaled only the second result; friendly tests hid
  this with `dict.setdefault`.
- **Required good observation:** a typed immutable effect is INTENT_RECORDED before
  dispatch; restart observes/reconciles exact remote state and confirms `pr-1`
  without a second create, or blocks on conflict.
- **Negative-control mutant:** use a generic callback checkpoint or an in-memory
  `setdefault` fake with no external observation. The new-process spy test must
  fail it.
- **Provenance and closure:** first QA CONFIRMED; first RCA independently
  reproduced duplicate Draft creation; second QA OPEN; RCA A/B required a typed
  outbox rather than assertion repair. Never closed.

### Finding 07: ordinary bilingual verify never compared the pair

- **Severity:** Critical, false-green semantic verification.
- **Original failure:** ordinary bilingual verify bundled each locale separately,
  so the critic never received one RU/EN pair and unrelated texts could PASS.
- **Contract mapping:** §23.9 and §23.11; §24.6.3, §24.9.2 and §24.11;
  NG-AC-142–150; §25.3.6.
- **Minimal reproduction/input:** open ordinary PR containing RU `# новый ru` and
  unrelated EN `# unrelated English` for one canonical path; invoke the full
  production router with a critic spy.
- **Observed bad output:** PASS with zero critic calls. Production compared
  uppercase `Locale.value` (`RU`/`EN`) to lowercase strings, making ordinary
  bilingual `model_capable` false; even beyond that gate, bundles were per-locale.
- **Required good observation:** exactly one critic unit contains both exact
  locale bytes for the canonical pair, and unrelated meaning yields a red
  semantic issue. A one-locale PR remains deterministic missing-translation red
  with zero critic calls.
- **Negative-control mutant:** restore the uppercase/lowercase comparison or call
  the critic once per locale without its peer. The black-box pair test must fail.
- **Provenance and closure:** first QA CONFIRMED; initially second QA marked
  PARTIAL, but RCA A/B production reproduction reopened it as OPEN after observing
  PASS/0 calls. Never closed.

### Finding 08: redirect planner and locale validator contradicted each other

- **Severity:** Major, deterministic publication blocker.
- **Original failure:** `validate_redirect_locale` rejected an EN-old to EN-new
  redirect that the planner itself emitted.
- **Contract mapping:** §23.4 and §23.5 redirect invariant; §24.10.7;
  NG-AC-125–131.
- **Minimal reproduction/input:** plan a same-locale target redirect
  `/en/old -> /en/new`, then pass the exact planned append through final
  validation/composition.
- **Observed bad output:** valid planner output was rejected by its downstream
  locale validator.
- **Required good observation:** planner and validator share one canonical locale
  rule; valid same-target-locale redirects survive final validation, while true
  cross-locale redirects remain red.
- **Negative-control mutant:** swap the target locale or reinstate the validator's
  contradictory comparison. Exactly the invalid case must fail.
- **Provenance and closure:** first QA and first RCA CONFIRMED; second QA CLOSED;
  RCA found no contrary evidence. Current clean restart still requires the frozen
  observation.

### Finding 09: TOC regex misparsed field order and nesting

- **Severity:** Major, structural TOC corruption.
- **Original failure:** the TOC regex required `name` before other fields and a
  parent match captured a child's `href`.
- **Contract mapping:** §23.5; §24.10.4–§24.10.6; NG-AC-117–125.
- **Minimal reproduction/input:** lossless YAML with `href` before `name`, nested
  `items`, comments/anchors and distinct parent/child hrefs; parse, apply one
  scoped delta and render.
- **Observed bad output:** valid reordered nodes were missed and parent identity
  was assigned the child's href.
- **Required good observation:** CST/tree parsing preserves field order, nesting,
  comments and service fields; the scoped edit addresses the exact node only.
- **Negative-control mutant:** replace the parser with the original order-sensitive
  regex or flatten a child into its parent. Golden bytes/tree assertions fail.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA CLOSED; no contrary
  RCA evidence. Acceptance obligation retained.

### Finding 10: the PR 45949 regression fixture was invented

- **Severity:** Critical, false recorded-case evidence.
- **Original failure:** the claimed PR 45949 fixture was an invented two-file YQL
  move, while the real PR had eight files and moved `node-authorization`.
- **Contract mapping:** §23.4–§23.5; §24.7 and §24.10; NG-AC-035–036, 122 and
  125–131; §24.19 `pr-45949-move`; §25.4.3.
- **Minimal reproduction/input:** immutable recorded GitHub PR/files payload and
  exact eight blobs for PR 45949, including deletion of
  `ydb/docs/ru/core/devops/deployment-options/manual/node-authorization.md` and
  addition of `ydb/docs/ru/core/devops/concepts/node-authorization.md` plus TOC
  and redirect evidence.
- **Observed bad output:** a synthetic YQL two-file case was labeled as PR 45949
  and allowed the regression row to pass.
- **Required good observation:** fixture identity/digests prove all eight real
  operations, and the black-box result mirrors the scoped EN move, target TOC and
  mandatory redirect behavior.
- **Negative-control mutant:** substitute any two-file or wrong-path payload while
  keeping the fixture name `45949`. Fixture provenance validation must fail before
  the target runs.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA CLOSED after a real
  overlay was reported; RCA found no contrary evidence. Because attempt 3's
  harness was rejected, clean restart must independently freeze it again.

### Finding 11: pagination accepted an incomplete manifest

- **Severity:** Major, immutable manifest completeness.
- **Original failure:** pagination accepted an empty intermediate page and had no
  page cap or repeated-cursor guard.
- **Contract mapping:** §23.1 official manifest; §24.7.1; NG-AC-028–031.
- **Minimal reproduction/input:** API sequence page 1 non-empty, page 2 empty with
  next link, page 3 non-empty; separately repeat one cursor forever and exceed a
  fixed safe cap.
- **Observed bad output:** iteration stopped at the empty page or could loop
  indefinitely, yet the manifest was accepted as complete.
- **Required good observation:** follow authoritative pagination links through all
  pages; reject cursor repetition, contradictory termination and cap overflow
  before snapshots/models.
- **Negative-control mutant:** treat `items == []` as terminal regardless of next
  link. The page-3 sentinel file must be reported missing and fail the finding.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA CLOSED; retained as
  a mandatory recorded adapter observation.

### Finding 12: depth override could not materialize deeper blobs

- **Severity:** Major, operator decision was ineffective.
- **Original failure:** snapshot materialization always stopped at default depth
  3, so an allowed depth 5 continue decision still lacked depth-4/5 blobs.
- **Contract mapping:** §23.7 dependency depth and continue override; §24.8.3;
  NG-AC-068–078.
- **Minimal reproduction/input:** dependency chain of five files, default run
  reports depth overflow, then replay an authorized exact-root depth-5 decision.
- **Observed bad output:** planner accepted depth 5 but materialized only the
  default depth-3 snapshot and failed/missed deeper dependencies.
- **Required good observation:** planning first determines the effective bound and
  exact required blob set, then the pinned snapshot materializes every allowed
  depth-4/5 file from one SHA.
- **Negative-control mutant:** hard-code materialization depth 3 while reporting
  effective depth 5. The deepest sentinel bytes must be absent and fail.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA CLOSED; no contrary
  evidence, but external acceptance remains required.

### Finding 13: symlink blobs were accepted as documentation dependencies

- **Severity:** Major, snapshot/path safety.
- **Original failure:** git mode `0120000` was accepted as an ordinary dependency.
- **Contract mapping:** §23.7 and §23.15.1; §24.4.1 and §24.8.1–§24.8.3;
  NG-AC-041–044 and 068–078.
- **Minimal reproduction/input:** a scoped include/dependency path whose pinned
  tree entry is a symlink with blob bytes pointing outside or to another doc.
- **Observed bad output:** symlink target bytes/path were admitted as if mode
  `0100644` documentation content.
- **Required good observation:** only explicitly supported regular-file modes are
  materialized; symlink/submodule/special modes produce a typed blocking issue
  before parsing or model prompts.
- **Negative-control mutant:** ignore git mode and classify solely by filename
  extension. The symlink fixture must fail.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA CLOSED; retained
  because the new snapshot adapter must prove mode enforcement.

### Finding 14: continue commands did not share one canonical scope identity

- **Severity:** Major, operator instruction loss and attempt consumption.
- **Original failure:** locale-qualified continue paths did not match locale-free
  outstanding scopes; a pathless force became GENERAL_GUIDANCE, and edited
  comment body handling rejected the change instead of restarting selection and
  ACL.
- **Contract mapping:** §23.1 `doc_continue`, §23.2–§23.3; §24.6.2 and §24.6.4;
  NG-AC-045–064.
- **Minimal reproduction/input:** create one locale-free outstanding pair; submit
  every canonical RU/EN path form, a pathless force phrase, and an edited selected
  comment with the same ID but changed body/author metadata.
- **Observed bad output:** valid scoped instruction was unmatched and downgraded
  to guidance, consuming an attempt; edited comment could not restart safe
  selection/ACL.
- **Required good observation:** one ScopeKey codec is used by report, parser and
  planner; ambiguous pathless force consumes nothing; mutation of selected
  comment restarts selection plus both ACL checks before attempt consumption.
- **Negative-control mutant:** serialize outstanding scope without locale but
  parser output with locale, or silently map unmatched force to guidance. The
  exact decision and counter assertions fail.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA CLOSED; clean
  harness must retain all canonical forms and edited-comment race.

### Finding 15: expired Draft identity was unavailable without YDB lineage

- **Severity:** Major, expiry recovery and user instruction.
- **Original failure:** `PullIdentity` lacked PR body and the expired-marker
  parser, so an open NG Draft whose 14-day lineage row expired could not be
  identified and reported safely.
- **Contract mapping:** §23.14; §24.13.1 and §24.14.10; NG-AC-061–064 and
  166–168.
- **Minimal reproduction/input:** create an NG Draft with a valid non-secret body
  marker, expire/delete compact lineage, restart persistence, then apply
  `doc_verify` to the open Draft.
- **Observed bad output:** no body/marker was available to derive source and
  expiry; the required tombstone/blocking report could not be produced.
- **Required good observation:** actual GitHub Pull body is strictly parsed;
  expired marker creates a tombstone, removes only the verify label, reports red
  with source/expiry and instructs clean `doc_translate`; marker never restores
  verification context.
- **Negative-control mutant:** omit Pull body in the adapter or accept a malformed
  marker. Restarted real-adapter test must fail.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA PARTIAL. Memory
  reproduction passed, but no restarted YDB plus recorded GitHub contract proved
  production behavior. RCA A/B retained PARTIAL.

### Finding 16: one-locale verify was incorrectly considered model-capable

- **Severity:** Critical, deterministic missing-translation gate.
- **Original failure:** metadata marked a one-locale verify as model-capable even
  though it must deterministically report missing translation without a critic.
- **Contract mapping:** §23.11; §24.5.2, §24.6.3 and §24.9.2;
  NG-AC-018–020 and 148.
- **Minimal reproduction/input:** open PR changes only one eligible RU or EN path,
  with no single-language exemption; run with a critic spy and exhausted budget.
- **Observed bad output:** the run entered model/budget capability logic instead
  of deterministic `MISSING_LOCALE_TRANSLATION`.
- **Required good observation:** red missing-translation report, zero critic calls
  and zero paid-budget admission, for either locale direction.
- **Negative-control mutant:** set `model_capable = true` whenever any eligible
  locale file changes. The exhausted-budget fixture must wrongly deny and fail.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA CLOSED. Still a
  frozen clean-restart negative control, especially alongside Finding 07.

### Finding 17: internal and external verification hashes were operational

- **Severity:** Critical, duplicated cost and inconsistent verification.
- **Original failure:** internal and external verification of exact published
  bytes produced different cache hashes because manifest/pair/bundle operational
  identities were included.
- **Contract mapping:** §23.9 verification case identity; §24.11.2–§24.11.4;
  NG-AC-142–146; §25.3.6.
- **Minimal reproduction/input:** translate a model-free safe change, publish its
  exact Draft bytes, restart the process, reconstruct the open Draft and run
  `doc_verify` without changing semantic bytes or rules.
- **Observed bad output:** internal hash
  `25f76d...f1b` and external hash `24334e...372` differed solely through
  operational construction identity.
- **Required good observation:** one SemanticVerificationKey built from exact
  pair/dependency/delete/decision/glossary bytes and behavior/model versions is
  identical across internal and external construction; deterministic checks rerun
  and critic calls are zero on hit. Any semantic byte/rule/model change misses.
- **Negative-control mutant:** add run ID, PR ID, manifest ordinal, bundle ID or
  reconstruction-only target snapshot identity to the hash. Exact-Draft reuse
  must fail while a one-byte mutation still misses.
- **Provenance and closure:** first QA CONFIRMED; initially second QA PARTIAL; RCA
  A/B independently reproduced unequal hashes and reopened it as OPEN. Never
  closed.

### Finding 18: redirect registry parser accepted invalid trailing YAML

- **Severity:** Major, append-only registry integrity.
- **Original failure:** a regex accepted a valid-looking redirect prefix followed
  by invalid YAML tail.
- **Contract mapping:** §23.4 and mandatory redirect invariant; §24.10.7;
  NG-AC-125–131.
- **Minimal reproduction/input:** valid registry entries/comments/anchors followed
  by malformed indentation, an unterminated scalar or an unknown trailing node;
  parse and append one redirect.
- **Observed bad output:** regex validated only the matching prefix and silently
  ignored the invalid tail.
- **Required good observation:** full lossless YAML/CST parse consumes the complete
  document; invalid tail blocks append and preserves original bytes.
- **Negative-control mutant:** reintroduce prefix regex validation. The malformed
  sentinel tail must be accepted by the mutant and rejected by the harness.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA CLOSED; retained as
  parser and composition evidence.

### Finding 19: glossary scope ignored ordinal and visible-text boundaries

- **Severity:** Major, glossary corruption and false usage.
- **Original failure:** glossary insertion ignored target ordinal and usage used
  raw substring search, matching terms inside `NoSQL`, code and URLs.
- **Contract mapping:** §23.9.1 and §23.15.4; §24.10.8–§24.10.9;
  NG-AC-132–141, 151 and 153.
- **Minimal reproduction/input:** glossary entries with anchors and fixed order;
  prose with an exact visible term plus negative occurrences inside a larger word,
  fenced/inline code and URL; compose one addition at a specified ordinal.
- **Observed bad output:** additions were appended at EOF and raw title substring
  matches expanded glossary work from non-visible/non-term contexts.
- **Required good observation:** AST visible-text walker triggers only allowed
  term boundaries; entry identity/ordinal places the lossless edit at its exact
  anchor interval while composing disjoint edits.
- **Negative-control mutant:** search raw Markdown bytes and append all additions
  at EOF. Both false usage and ordering assertions must fail.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA CLOSED; clean
  acceptance keeps both positive and negative text contexts.

### Finding 20: candidate-only internal fragment was assumed valid

- **Severity:** Major, broken-link false green.
- **Original failure:** an internal fragment on a candidate target was accepted
  without reading/verifying the candidate bytes.
- **Contract mapping:** §23.8 internal links; §24.10.2 and §24.11.3;
  NG-AC-108–110 and 142–154.
- **Minimal reproduction/input:** candidate A links to candidate B `#missing`;
  B's planned path exists but its exact bytes contain no matching explicit or
  generated fragment.
- **Observed bad output:** path membership alone made the fragment valid.
- **Required good observation:** fragment validity is derived from the exact final
  target bytes after composition; missing target fragment preserves the working
  source link with the specified warning or blocks according to the applicable
  link rule.
- **Negative-control mutant:** return true whenever target path is in the overlay.
  The missing-fragment fixture must fail.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA CLOSED; retained
  with exact-byte evidence.

### Finding 21: critic issue conversion discarded actionable evidence

- **Severity:** Major, report correctness and repair routing.
- **Original failure:** conversion from `CriticIssue` lost source/target paths and
  the ready action.
- **Contract mapping:** §23.13; §24.4 Evidence/VerificationIssue and §24.14;
  NG-AC-159–164.
- **Minimal reproduction/input:** critic returns one structured issue with distinct
  source/target paths, exact fragments/lines and a valid ready continue action;
  render the canonical Russian report and repair payload.
- **Observed bad output:** converted issue omitted path provenance and actionable
  command, preventing precise report/repair behavior.
- **Required good observation:** all typed evidence survives conversion with exact
  checked-byte digest, reliable locations and concrete Russian action; no field is
  reconstructed from prose.
- **Negative-control mutant:** drop either source path, target path or action in
  the converter. Schema/report equality must fail.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA CLOSED; retained as
  an end-to-end structured-report contract.

### Finding 22: one composer error discarded independent safe bundles

- **Severity:** Critical, atomic safe publication.
- **Original failure:** a composer `ValueError` became global TECHNICAL_FAILURE
  and lost otherwise safe independent bundles.
- **Contract mapping:** §23.10 atomic safe publication; §24.8.6 and
  §24.11.5; NG-AC-151–154.
- **Minimal reproduction/input:** two independent bundles: one valid document and
  one malformed/conflicting TOC/redirect/glossary composition; run final overlay
  composition and publication.
- **Observed bad output:** the local composition exception aborted the entire run
  and no safe bundle remained publishable.
- **Required good observation:** typed composition failure attaches to all and
  only affected/dependent bundles; independent complete safe bundle publishes in
  a red Draft and both sets are reported.
- **Negative-control mutant:** catch a local composition error only at the global
  router and return TECHNICAL_FAILURE. The safe-bundle publication assertion must
  fail.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA CLOSED; clean
  harness retains multi-bundle isolation and shared-output counterexamples.

### Finding 23: workflow ingress and Actions identity were incomplete

- **Severity:** Major, production workflow/identity contract.
- **Original failure:** workflows persisted event PR with lineage `none`, used
  `paths: ydb/docs/**` so outside-scope informational runs were suppressed, and
  later passed undeclared `merge_base_with` to the action.
- **Contract mapping:** §23.1 labels/concurrency and §23.15.1; §24.5.1,
  §24.5.3 and §24.15; NG-AC-009, 023–027 and 068; §25.4.1.
- **Minimal reproduction/input:** validate all committed workflows/action inputs;
  send labeled events for source PR, NG Draft with different source lineage and
  outside-doc/unsupported path; paginate Actions runs through page 3.
- **Observed bad output:** exact source/lineage was absent or provisional in
  production identity, outside-scope label runs never started, and workflows
  supplied an input absent from `action.yml`.
- **Required good observation:** labeled-only workflows have no path suppression;
  every `with` key is declared; event PR is provisional display only; resolved
  source/lineage is persisted and joined to all paginated runs with deterministic
  precedence and exact conflict link.
- **Negative-control mutant:** add a `paths` filter, undeclared input, or parse
  lineage from run-name. Workflow-schema and recorded Actions tests must fail.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA PARTIAL because
  run-name semantics improved but the undeclared input and production adapter
  contract remained. RCA A/B retained PARTIAL.

### Finding 24: doc_verify composition held excessive capabilities

- **Severity:** Critical, read-only capability safety.
- **Original failure:** `doc_verify` composition received a full write-capable
  GitHub port and read-write workspace despite the read-only product contract.
- **Contract mapping:** §23.1 `doc_verify` and §23.11; §24.5.1, §24.6.3 and
  §24.13.2; NG-AC-156; §25.3.7.
- **Minimal reproduction/input:** construct the exact production verify worker,
  enumerate/instrument its ports and filesystem mount, then exercise success and
  every technical failure path.
- **Observed bad output:** first implementation exposed broad GitHub/workspace
  write capability; first RCA classified PARTIAL because labels/comments are
  legitimate control writes but repository-content writes are not.
- **Required good observation:** verify receives immutable content readers plus a
  narrow control-plane port limited to exact command-label removal and canonical
  comment update. No branch, commit, push, PR create/delete/metadata or read-write
  workspace capability is constructible/reachable.
- **Negative-control mutant:** inject a broad GitHub client or writable workspace
  and leave forbidden methods unused. Capability inspection must still fail.
- **Provenance and closure:** first QA PARTIAL, not refuted; first RCA agreed the
  capability distinction; second QA CLOSED after narrow composition was reported.
  Clean restart must prove it from the release image, not call-spy absence alone.

### Finding 25: one completion event used multiple clocks

- **Severity:** Major, expiry/audit identity.
- **Original failure:** completion/expiry used multiple `clock.now()` calls, so
  marker, lineage and audit timestamps could diverge.
- **Contract mapping:** §23.14; §24.4.1, §24.6 and §24.12;
  NG-AC-166–168 and 175; §25.3.3.
- **Minimal reproduction/input:** inject a stepping clock returning a distinct
  instant on every call; run success, warning, blocker, rejection and technical
  failure through real persistence.
- **Observed bad output:** application marker/lineage/finished timestamps were
  later aligned in memory, but YDB lineage, cache, journal and audit writes still
  called ambient `datetime.now()` independently.
- **Required good observation:** one captured completion instant is passed into
  every result, marker, lineage, TTL, cache, journal and audit write belonging to
  that completion; only explicitly different events get different timestamps.
- **Negative-control mutant:** call ambient time in one persistence adapter while
  application uses the injected clock. Cross-store equality must fail.
- **Provenance and closure:** first QA/RCA CONFIRMED; second QA PARTIAL. RCA A/B
  confirmed application-level improvement but unresolved adapter clocks.

### Finding 26: persisted manifest and model DTOs accepted corrupt values

- **Severity:** Major, strict boundary/state safety.
- **Original failure:** `SourceManifest` accepted non-lowercase/invalid SHAs,
  naive datetimes and bad digest; later `ModelAttemptRecord` also accepted empty
  IDs, negative indices/tokens/cost, bad digests, contradictory outcome/failure,
  naive timestamps and finish-before-start.
- **Contract mapping:** §23.1 immutable manifest and §23.14 audit; §24.4–§24.4.1
  and §24.12.1; NG-AC-008, 028–031, 104, 143 and 166–169; §25.3.2.
- **Minimal reproduction/input:** construct and deserialize each DTO with exactly
  one invalid field: empty identity, uppercase/short SHA, wrong content digest,
  negative ordinal/index/token/cost, invalid enum/nullability, naive time or
  reversed interval. Repeat through stored YDB rows, not only constructors.
- **Observed bad output:** every listed corrupt record was accepted without a
  typed validation failure.
- **Required good observation:** frozen closed DTO constructors and untrusted-row
  decoders reject each mutation before state transition/policy; corrupt persisted
  state yields a typed technical terminal report and no defaulting.
- **Negative-control mutant:** bypass `__post_init__`/schema validation in the YDB
  adapter or normalize invalid values silently. Property tests must expose at
  least one accepted mutant and fail.
- **Provenance and closure:** first QA/RCA CONFIRMED for SourceManifest; second QA
  OPEN after independent ModelAttemptRecord invalid-value construction; RCA A/B
  classified strict boundary validation as a systemic remaining cause. Never
  closed.

## 26.2 Consolidated closure history

| Review point | CLOSED | PARTIAL | OPEN/CONFIRMED |
|---|---|---|---|
| First independent QA + first RCA | none | 24 | 01–23, 25–26 |
| Second independent QA after remediation | 08–14, 16, 18–22, 24 | 02, 05, 15, 23, 25 | 01, 03, 04, 06, 07, 17, 26 |
| Third attempt acceptance review | no individual closure accepted | none accepted | all 01–26 remain clean-restart obligations because the harness itself failed independence controls |

The second-QA row records historical implementation behavior only. It does not
permit the clean-restart harness to omit a finding. Reopening of 07 and 17 is
material: both were initially treated as partial until full production-path RCA
showed an ordinary bilingual false green with zero critic calls and distinct
semantic hashes for identical internal/external bytes.

## 26.3 Phase 0 import rule

The acceptance repository must copy this ledger as reviewed source material, then
encode each finding as an independently authored static fixture plus executable
observation. It must not parse this Markdown at runtime to generate expected
predicates. Every `finding_01` through `finding_26` must:

1. fail against the contract stub and its finding-specific negative-control
   mutant for the stated semantic reason;
2. run the supplied release executable/image as a separate process;
3. control failures only through external provider, GitHub, YDB, artifact,
   filesystem and clock boundaries;
4. retain fixture provenance, inputs, observed outputs and exact assertion in the
   signed acceptance result;
5. remain unchanged while product implementation is reviewed.

No AC name, test name, coverage percentage or implementation DTO equality may
substitute for the required good observation above.

---

[Back to Memory Bank index](../../MEMORY_BANK.md)
