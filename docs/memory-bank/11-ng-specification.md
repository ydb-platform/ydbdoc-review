# Memory Bank: simplified NG behavioral specification

> Part of the [Memory Bank index](../../MEMORY_BANK.md).
> Behavioral and protocol handoff for the §23 product contract. The failed
> in-repository implementation topology and work order are superseded by §25.

---

## 24. Status, authority and version

- **Status:** BEHAVIORAL SPEC PASS; IMPLEMENTATION NOT ACCEPTED
- **Specification version:** 1.0.3
- **Specification date:** 2026-08-28
- **Product authority:** [§23 NG requirements](10-ng-requirements.md)
- **Implementation strategy:** CLEAN_ROOM_DISTRIBUTION, as fixed by §23.17 and
  §25
- **Recovery point:** tag **pre-ng-2026-08-27**, commit
  **1f04ab1c71488f53c4ad547c20c7e635d59696ad**

**Review note for v1.0.3:** the second independent RCA proved that unconditional
post-response durability cannot be guaranteed over the configured synchronous
provider APIs: the process can die after the provider accepts a paid request but
before its result reaches YDB. §24 now specifies the derived §23 safety rule with
durable pre-request state, at-most-once dispatch, explicit `UNKNOWN_BILLED`, a
current-Moscow-day paid-work gate and authoritative reconciliation. No usage or
cost is invented. Version 1.0.2's implementability correction remains in force:
top-level Actions run-name contains provisional event PR metadata only, while
persisted identity is authoritative for source/lineage matching.

**Clean-restart note for 2026-08-28:** all three implementations built from this
handoff failed independent acceptance. Their code and in-tree tests were deleted.
The §24 behavioral rules, strict DTO definitions, `UNKNOWN_BILLED` protocol and
NG-AC semantics remain the frozen contract baseline. The in-repository package
topology in §24.2, legacy-reuse allowance, migration order in §24.17,
topology-specific NG-AC-001..003 wording and implementation-first work breakdown
in §24.20 are historical and MUST NOT be followed. §25 replaces them with a new
distribution, no legacy import closure and an independently reviewed red-first
acceptance harness. Cutover and NG `doc_translate` are prohibited meanwhile.

This document is normative for behavior, DTOs and protocols except where §25
explicitly replaces topology, test ownership, work order or migration mechanics.
It MUST NOT change a product decision from §23. If this document and §23
conflict, §23 wins and implementation MUST stop until the specification is
corrected.
Historical §6, §15–§17 and §22 behavior is regression evidence only. It MUST NOT
be imported into NG as an unstated requirement.

The terms MUST, MUST NOT, REQUIRED, SHOULD, SHOULD NOT and MAY have their RFC 2119
meaning. Unless explicitly marked optional, every field and transition below is
required.

### 24.0.1 Vocabulary

- **source PR**: the original merged pull request whose Pull Request Files API
  response defines the immutable operation manifest.
- **translation PR** or **Draft**: the bot-created Draft pull request containing
  safe NG output for one source PR.
- **lineage**: the persistent history beginning with one accepted
  **doc_translate** and containing its decisions and up to three accepted
  **doc_continue** runs.
- **current-main snapshot**: the immutable tree identified by the exact SHA of
  **main** captured once after gates at run start.
- **official merge snapshot**: the source PR identity tuple containing
  **merge_commit_sha**, **base.sha** and **head.sha**, plus every paginated PR file
  record.
- **canonical locale pair**: two paths differing only in the first component
  immediately below **ydb/docs**, **ru** versus **en**.
- **manifest operation**: an independent ADD, UPDATE or DELETE derived only from
  the GitHub API. RENAME expands to DELETE plus ADD.
- **root operation**: one manifest operation or one recognized direct TOC or
  glossary operation that starts an atomic bundle.
- **dependency closure**: the bounded set reached from one root document through
  allowed parsed edges.
- **operation bundle**: the atomic publication unit rooted at one scoped
  document or one standalone supported root operation.
- **candidate overlay**: immutable writes, deletes, TOC edits, redirect appends
  and glossary entry edits proposed over the snapshot without changing a
  worktree.
- **safe diff**: the union of complete bundles that have no blocking issue
  attached to the bundle or a mandatory shared dependency.
- **operator decision**: a typed, lineage-scoped answer extracted from an
  authorized continue comment.
- **model attempt**: one durable call identity for which the provider adapter is
  invoked at most once. Provider adapters MUST NOT retry internally. An attempt
  may end with a recorded result or with unknown billing after an ambiguous
  process failure.
- **verification pass**: deterministic validation of the complete case plus one
  critic attempt sequence for every critic unit.
- **terminal report**: the canonical Russian comment updated after every accepted
  or rejected command event.

## 24.1 Goals, non-goals and assumptions

### 24.1.1 Goals

NG MUST:

1. implement every rule in §23 without inheriting contradictory legacy policy;
2. make the operation manifest, snapshot, decisions and verification case
   immutable and reproducible;
3. produce only complete deterministic-safe atomic bundles;
4. use one shared verification service in translate, continue and verify;
5. make every external mutation idempotent and auditable;
6. give a developer exact state transitions and give an independent tester
   observable inputs, outputs and failure results;
7. retain enough state for deterministic destructive rebuilds for 14 days;
8. fail closed where authority, critic validity, redirects, dependencies or
   publication safety are unknown, and fail open only where §23 explicitly says
   so.

### 24.1.2 Non-goals

NG v1 MUST NOT:

- preserve target-only prose, perform a historical delta merge or infer a move;
- scan the repository globally for inbound links, parity debt or neighboring
  files;
- translate or inspect single-language content;
- run, wait for or interpret the external documentation build;
- change Draft to Ready, merge a PR or edit a human branch;
- auto-resolve ambiguous authority, redirect, TOC placement, glossary identity,
  image ownership or shared-dependency direction;
- probe arbitrary external URLs or use OCR;
- maintain a second terminology source outside the documentation glossaries;
- create a pending-link registry or a later cleanup PR;
- expose an unbounded retry, hidden provider fallback or approximate report
  location as exact;
- run legacy and NG writers in parallel.

### 24.1.3 Assumptions fixed for implementation

- Repository identity is the lowercase **owner/name** string. PR numbers are
  positive integers.
- Repository paths are normalized POSIX paths with no leading slash, dot
  component, parent escape, NUL or duplicate separator.
- Git object IDs and content digests are lowercase hexadecimal strings.
- All timestamps are UTC RFC 3339 with microseconds. Moscow-day budget boundaries
  use IANA zone **Europe/Moscow**.
- Stable serialization is UTF-8 canonical JSON: sorted object keys, no insignificant
  whitespace, integers in decimal, timestamps normalized as above, arrays kept in
  defined order.
- Collections inside domain values are tuples or sorted key-value tuples. Mutable
  dictionaries and lists MUST NOT cross a domain or port boundary.
- All snapshot reads address an exact commit SHA. An ambient checkout MAY be used
  only inside an adapter that proves the read SHA.

## 24.2 Package architecture and import boundaries

The v1.0.3 in-repository package topology and exact-symbol legacy reuse model were
exercised by three failed attempts and are withdrawn. They are not retained here
as a template.

The effective architecture is §25.2 through §25.4:

- a separate `ydbdoc-review-ng` repository and `ydbdoc_ng` distribution;
- a separate `ydbdoc-review-ng-acceptance` repository;
- black-box process or OCI testing with no production imports;
- no `ydbdoc_review` or failed-NG dependency anywhere in the source, build or
  runtime closure;
- a strict domain/application/verifier/control-plane/ports/adapters structure;
- an independently reviewed red acceptance harness before product coding.

The model port remains at-most-once. One invocation performs at most one provider
request and never retries internally. Optional authoritative reconciliation
returns only an exact recovered result, proof of non-acceptance/non-billing, or
INCONCLUSIVE. The application owns selection, rotation, transcript, call-state
and cost behavior.

## 24.3 Normative enums

All enum values are serialized exactly as shown:

~~~text
Command = DOC_TRANSLATE | DOC_CONTINUE | DOC_VERIFY
Locale = RU | EN
Direction = RU_TO_EN | EN_TO_RU | NONE | UNRESOLVED
ManifestStatus = ADDED | MODIFIED | REMOVED | RENAMED
ManifestOp = ADD | UPDATE | DELETE
PairClass =
  SINGLE_RU_WRITE | SINGLE_EN_WRITE | SINGLE_RU_DELETE | SINGLE_EN_DELETE |
  BILINGUAL_WRITE | BILINGUAL_DELETE | DELETE_RU_WRITE_EN |
  DELETE_EN_WRITE_RU | SINGLE_LANGUAGE | UNSUPPORTED | SUPERSEDED
AuthorityVerdict = NO_TRANSLATION | RU_AUTHORITY | EN_AUTHORITY | AMBIGUOUS
SemanticNoopVerdict = NO_TRANSLATION | TRANSLATION_REQUIRED | UNCERTAIN
ArtifactKind =
  MARKDOWN | MARKDOWN_INCLUDE | TOC | REDIRECT_REGISTRY | IMAGE |
  YAML_COMPANION | JSON_COMPANION | TXT_COMPANION | CPP_COMPANION |
  GLOSSARY_ENTRY
RootKind = DOCUMENT | TOC | GLOSSARY | IMAGE | COMPANION | DELETE
DependencyKind = INCLUDE | IMAGE | COMPANION_LINK
TocOpKind =
  ADD_NODE | REMOVE_NODE | MOVE_NODE | CHANGE_LABEL | CHANGE_HREF |
  CHANGE_INCLUDE_PATH | CREATE_TARGET_TOC | DELETE_TARGET_TOC
RedirectEvidenceKind = SOURCE_REGISTRY | SAME_TOC_POSITION | OPERATOR
DecisionKind =
  FORCE_TRANSLATION | PAIR_AUTHORITY | IMAGE_AUTHORITY |
  SHARED_DEPENDENCY_AUTHORITY | GLOSSARY_AUTHORITY |
  GLOSSARY_ENTRY_MAPPING | REDIRECT_TARGET | TOC_PLACEMENT |
  URL_REPLACEMENT | DEPTH_LIMIT | MIXED_PAIR_OUTCOME |
  RETRY_CLASSIFICATION | GENERAL_GUIDANCE
LineageState =
  WAITING_NO_DRAFT | DRAFT_OPEN | READY_BLOCKED | DRAFT_CLOSED |
  DAMAGED | MERGED | EXPIRED | REPLACED
RunPhase =
  RECEIVED | LABEL_REMOVED | LOCK_ACQUIRED | GATES_PASSED |
  SNAPSHOT_CAPTURED | PLANNED | MODEL_RUNNING | VERIFIED |
  PUBLICATION_STARTED | PUBLISHED | REPORTED | TERMINAL
RunOutcome =
  PASS | PASS_WITH_WARNINGS | BLOCKED | REJECTED |
  ALREADY_COMPLETE | NO_SUPPORTED_SCOPE | TECHNICAL_FAILURE
Severity = BLOCKED | WARNING | INFO
Verdict = BLOCKED | PASS_WITH_WARNINGS | PASS
IssueClass = MODEL_REPAIRABLE | OPERATOR_REQUIRED | DETERMINISTIC |
  OPERATIONAL
BundleState = PLANNED | CANDIDATE | SAFE | OMITTED | PUBLISHED | NOOP
ModelRole = CLASSIFIER | TRANSLATOR_A | CRITIC_B | REPAIR_B |
  CRITIC_A | REPAIR_A | FINAL_CRITIC_B
ModelCallState =
  RESERVED | RESULT_RECORDED | RECONCILED_NOT_BILLED | UNKNOWN_BILLED
CallOutcome =
  SUCCESS | TIMEOUT | PROVIDER_ERROR | MALFORMED | FORMAT_REPAIR_FAILED |
  FALLBACK_EXHAUSTED
~~~

No extra enum value MAY be treated as a known value. Unknown stored values block
recovery and produce a technical report rather than a guessed transition.

## 24.4 Immutable domain models and DTOs

The following Python-like declarations define field names, types and nullability.
They are schemas, not implementation code. Every declaration is frozen and uses
tuples. **FrozenMap[K,V]** means a tuple sorted by canonical serialized key.

~~~text
RepoId(owner: str, name: str)

CommandEvent(
  delivery_id: str,
  label_timeline_event_id: int,
  actions_run_id: int,
  command: Command,
  repository: RepoId,
  event_name: str,
  event_action: str,
  pr_number: int,
  label_name: str,
  sender_login: str,
  received_at: datetime,
  raw_event_sha256: Digest
)

PullIdentity(
  number: int,
  state: OPEN | CLOSED,
  merged: bool,
  draft: bool,
  merge_commit_sha: GitSha | None,
  base_sha: GitSha,
  head_sha: GitSha,
  head_ref: str,
  html_url: str,
  updated_at: datetime
)

ManifestEntry(
  ordinal: int,
  path: RepoPath,
  status: ManifestStatus,
  previous_path: RepoPath | None,
  additions: int | None,
  deletions: int | None,
  blob_sha: GitSha | None
)

SourceManifest(
  repository: RepoId,
  source_pr: int,
  merge_commit_sha: GitSha,
  base_sha: GitSha,
  head_sha: GitSha,
  fetched_at: datetime,
  page_count: int,
  entries: tuple[ManifestEntry, ...],
  manifest_sha256: Digest
)

SnapshotRef(
  repository: RepoId,
  ref_name: str,
  commit_sha: GitSha,
  captured_at: datetime,
  tree_sha: GitSha
)

FileImage(
  path: RepoPath,
  git_mode: int,
  bytes: bytes,
  size: int,
  sha256: Digest
)

MaterializedSnapshot(
  ref: SnapshotRef,
  files: FrozenMap[RepoPath, FileImage],
  missing_paths: tuple[RepoPath, ...]
)

CanonicalPairKey(relative_path: RepoPath)

ExpandedOperation(
  manifest_ordinal: int,
  op: ManifestOp,
  locale: Locale | None,
  path: RepoPath,
  pair_key: CanonicalPairKey | None,
  derived_from_rename: bool
)

PairOperation(
  key: CanonicalPairKey,
  ru_operations: tuple[ExpandedOperation, ...],
  en_operations: tuple[ExpandedOperation, ...],
  pair_class: PairClass,
  current_ru: FileImage | None,
  current_en: FileImage | None,
  direction: Direction,
  authority: AuthorityVerdict | None,
  applicable: bool,
  reason_code: str
)

DependencyEdge(
  root_path: RepoPath,
  from_path: RepoPath,
  to_path: RepoPath,
  kind: DependencyKind,
  depth: int,
  source_line: int,
  raw_reference: str
)

DependencyClosure(
  root_path: RepoPath,
  effective_max_depth: int,
  unique_dependency_count: int,
  edges: tuple[DependencyEdge, ...],
  files: tuple[RepoPath, ...],
  cycle_edges: tuple[DependencyEdge, ...],
  overflow_chains: tuple[tuple[RepoPath, ...], ...]
)

Write(path: RepoPath, bytes: bytes, sha256: Digest, source_path: RepoPath)
Delete(path: RepoPath, expected_snapshot_sha256: Digest | None)
TocEdit(toc_path: RepoPath, op: TocOpKind, node_id: str, before: bytes | None,
        after: bytes | None, required_redirect_id: str | None)
RedirectAppend(old_href: str, new_href: str, evidence: RedirectEvidenceKind,
               evidence_ref: str)
GlossaryEdit(glossary_path: RepoPath, entry_id: str,
             before: bytes | None, after: bytes | None,
             desired_ordinal: int | None, source_entry_locator: str)

OperationBundle(
  bundle_id: str,
  root_kind: RootKind,
  root_path: RepoPath,
  pair_key: CanonicalPairKey | None,
  direction: Direction,
  closure: DependencyClosure | None,
  writes: tuple[Write, ...],
  deletes: tuple[Delete, ...],
  toc_edits: tuple[TocEdit, ...],
  redirects: tuple[RedirectAppend, ...],
  glossary_edits: tuple[GlossaryEdit, ...],
  mandatory_bundle_ids: tuple[str, ...],
  shared_dependency_keys: tuple[CanonicalPairKey, ...],
  state: BundleState
)

CandidateOverlay(
  base_snapshot: SnapshotRef,
  bundles: tuple[OperationBundle, ...],
  writes: FrozenMap[RepoPath, Write],
  deletes: FrozenMap[RepoPath, Delete],
  overlay_sha256: Digest
)

ModelIdentifier(provider: str, model: str)

OperatorDecision(
  decision_id: str,
  lineage_id: str,
  kind: DecisionKind,
  exact_scope_key: str,
  normalized_value: str,
  source_comment_id: int,
  source_comment_url: str,
  author_login: str,
  accepted_run_id: str,
  accepted_at: datetime
)

LineageSnapshot(
  lineage_id: str,
  repository: RepoId,
  source_pr: int,
  source_manifest: SourceManifest,
  state: LineageState,
  translation_pr: int | None,
  branch: str,
  continue_count: int,
  decisions: tuple[OperatorDecision, ...],
  consumed_comment_ids: tuple[int, ...],
  placeholder_map: FrozenMap[str, str],
  model_rotation_index: int,
  latest_translator_model: ModelIdentifier | None,
  latest_critic_model: ModelIdentifier | None,
  latest_verification_case_sha256: Digest | None,
  latest_main_snapshot: SnapshotRef | None,
  created_at: datetime,
  expires_at: datetime,
  revision: int
)

Evidence(
  source_path: RepoPath | None,
  target_path: RepoPath | None,
  commit_sha: GitSha | None,
  checked_content_sha256: Digest,
  start_line: int | None,
  end_line: int | None,
  heading_or_path: str | None,
  exact_fragment: str | None,
  source_fragment: str | None,
  target_fragment: str | None
)

SuggestedAction(
  russian_text: str,
  continue_command: str | None,
  correct_comment_pr: int | None
)

VerificationIssue(
  issue_id: str,
  rule_id: str,
  severity: Severity,
  issue_class: IssueClass,
  bundle_ids: tuple[str, ...],
  evidence: tuple[Evidence, ...],
  russian_message: str,
  action: SuggestedAction,
  repair_payload: bytes | None
)

ModelAttemptRecord(
  run_id: str,
  call_id: str,
  operation_id: str,
  role: ModelRole,
  pass_index: int,
  attempt_index: int,
  primary: ModelIdentifier,
  actual: ModelIdentifier,
  prompt_version: str,
  request_sha256: Digest,
  state: ModelCallState,
  provider_request_id: str | None,
  reconciliation_evidence_sha256: Digest | None,
  response_sha256: Digest | None,
  outcome: CallOutcome | None,
  failure_code: str | None,
  input_tokens: int | None,
  output_tokens: int | None,
  cost_rub: Decimal | None,
  reserved_at: datetime,
  reservation_moscow_day: date,
  finished_at: datetime | None
)

VerificationCase(
  case_version: str,
  source_snapshot: MaterializedSnapshot,
  target_snapshot: MaterializedSnapshot,
  manifest: SourceManifest | None,
  pair_operations: tuple[PairOperation, ...],
  scopes: tuple[DependencyClosure, ...],
  directions: FrozenMap[CanonicalPairKey, Direction],
  candidate: CandidateOverlay,
  pending_deletes: tuple[Delete, ...],
  operator_decisions: tuple[OperatorDecision, ...],
  glossary_snapshots: tuple[FileImage, FileImage],
  verifier_version: str,
  deterministic_rule_versions: FrozenMap[str, str],
  prompt_versions: FrozenMap[ModelRole, str],
  model_identifiers: FrozenMap[ModelRole, ModelIdentifier],
  case_sha256: Digest
)

VerificationResult(
  case_sha256: Digest,
  checked_commit_sha: GitSha | None,
  verdict: Verdict,
  issues: tuple[VerificationIssue, ...],
  safe_bundle_ids: tuple[str, ...],
  omitted_bundle_ids: tuple[str, ...],
  model_attempts: tuple[ModelAttemptRecord, ...],
  deterministic_metrics: FrozenMap[str, Decimal],
  rendered_report_sha256: Digest
)
~~~

### 24.4.1 Model invariants

1. **size == len(bytes)** and **sha256 == SHA256(bytes)** for every FileImage and
   Write.
2. Manifest ordinals are contiguous from zero and preserve API page and item
   order.
3. A RENAMED manifest entry has a non-null previous path and expands into exactly
   two operations: DELETE at ordinal sub-index zero and ADD at sub-index one.
4. SourceManifest SHA covers the identity SHAs and every entry, not fetch time.
5. Snapshot files all belong to the one snapshot SHA. Missing paths are explicit,
   not represented by empty bytes.
6. CanonicalPairKey contains no locale component.
7. A resolved direction has exactly one authoritative source locale and the
   opposite target locale.
8. A Write and Delete for the same path in one overlay is invalid.
9. Identical writes from multiple bundles deduplicate by path and digest.
   Different bytes for the same path are a blocking shared-output conflict.
   GlossaryEdit is the sole exception: disjoint safe entry edits are composed by
   §24.10.9 into one final file Write and never appear as competing direct Writes.
10. A TOC href removal has exactly one required redirect in the same bundle.
11. A published bundle includes every transitive mandatory bundle.
12. Decisions are append-only within a lineage. A later decision with the same
    exact scope key supersedes for evaluation but does not erase audit history.
13. Continue count is 0 through 3 and increases only on an accepted continue.
14. Lineage expiry and run/artifact expiry are independent.
15. Verification result order is stable: bundle manifest order, path, rule ID,
    then evidence location.
16. A PASS result has no BLOCKED or WARNING issue. PASS_WITH_WARNINGS has at least
    one WARNING and no BLOCKED. BLOCKED has at least one BLOCKED issue.
17. Actual cost is either returned by the provider or calculated from
   provider-returned usage with the persisted versioned tariff. If required
   usage is absent, cost is null. NG never invents missing usage.
18. Evidence is always bound to exact checked bytes by
    **checked_content_sha256**. An internal pre-publication result has null
    **commit_sha** and **checked_commit_sha**; binding that unchanged content to
    the created Draft commit fills those reporting fields without changing case
    identity or verification semantics. Every published or external-verify
    report has a non-null checked commit.
19. An active Draft lineage stores the exact latest A/B identifiers and final
    verification case hash. A later configuration reorder MUST NOT silently
    reinterpret **model_rotation_index** as a different historical model pair.
20. A ModelAttemptRecord starts as **RESERVED** with null outcome, response,
    tokens, cost and finished_at. The exact call ID may be dispatched to its
    provider at most once. **RESULT_RECORDED** has a non-null outcome and
    finished_at. **RECONCILED_NOT_BILLED** has authoritative evidence, null
    response/tokens/cost, outcome PROVIDER_ERROR and a typed failure code.
    **UNKNOWN_BILLED** has
    null response/tokens/cost, a billing-unknown failure code and no inferred
    outcome. Unknown cost is never serialized as zero.
21. **reservation_moscow_day** is the Europe/Moscow calendar date containing
    reserved_at and never changes. Authoritative reconciliation evidence is
    content-addressed, secret-free and tied to the exact provider/call identity.

## 24.5 Event contract, gates, locks and terminal outcomes

### 24.5.1 Accepted GitHub inputs

Production command handling accepts only a GitHub pull-request **labeled** event:

- label **doc_translate** maps to DOC_TRANSLATE;
- label **doc_continue** maps to DOC_CONTINUE;
- label **doc_verify** maps to DOC_VERIFY.

The command actor is the top-level webhook **sender.login** from that exact
labeled event. It is not **pull_request.user.login**, the PR author or a service
account inferred from prior state.
The event delivery ID is the idempotency key. A workflow-dispatch or local CLI
MAY create the same CommandEvent only in an explicitly non-production environment
and MUST NOT obtain GitHub write ports.

An **issue_comment created or edited** event containing a line beginning exactly
with **/ydbdoc continue** does not start a command and does not call a model. It
is eligible input only when a later DOC_CONTINUE label event selects it. Selection
uses the latest unconsumed applicable comment by GitHub **created_at**, then
comment ID. Edited text is read at label time and its body digest is persisted.

### 24.5.2 Universal gate order

Every labeled event follows this exact order:

1. Validate repository, PR number, action, exact command label and delivery ID
   from the webhook payload, without consulting current label state.
2. Atomically claim **repository + delivery_id** by inserting the delivery and
   RECEIVED run rows, or load the already associated run. A duplicate resumes
   only that run's persisted phase and mutation journal. If it is terminal, return
   its recorded result. A duplicate MUST NOT remove a currently present label,
   acknowledge a newer event or create another run.
3. For a newly claimed delivery only, fully paginate the PR's GitHub
   issue/timeline events and bind the run to the stable ID of the currently active
   matching **labeled** event. The event ID, label, actor and PR must match the
   webhook; persist that ID into the delivery/run before removal. Inability to
   prove one exact event is a technical failure before label mutation.
4. Remove only that bound command-label instance. Failure to
   remove it is TECHNICAL_FAILURE and
   blocks all further work.
5. Update the one applicable canonical lifecycle/QA comment to an acknowledgement
   state carrying command and delivery ID. Do not create a per-delivery comment.
6. Resolve source-PR/lineage identity without reading content bytes. For
   DOC_CONTINUE, also select and hash the latest applicable comment metadata/body.
7. Apply ACL. DOC_CONTINUE checks both label sender and selected comment author.
   Missing or empty allowlist is configuration failure, never allow-all.
8. Apply command lifecycle gates: merged-only translate, open-only verify,
   expired-NG-Draft verify, and continue
   location/state/expiry/count/duplicate-Draft rules. For DOC_TRANSLATE, a
   previously merged translation returns ALREADY_COMPLETE here, and multiple
   active translation PRs reject here, before lock, budget or model configuration.
9. Query GitHub Actions for concurrent queued or in-progress NG command runs.
10. Acquire the per-source compare-and-set lock with a two-hour expiry.
11. Perform a metadata-only paid-work preflight. DOC_TRANSLATE constructs and
    persists the official manifest; DOC_CONTINUE reads its retained manifest;
    DOC_VERIFY fetches its stable fully paginated PR-files list. Apply only path,
    status, pair-shape, single-language and supported-type rules, without reading
    file content. Classify the run as **model_capable** exactly when those
    operations can create a classifier, translator or critic unit under §24.9.2.
12. If model_capable, read Moscow-day actual spend and unresolved
    **UNKNOWN_BILLED** rows. Reject paid work when actual spend is at or above
    budget or any unresolved unknown belongs to the current Moscow day. A proven
    deterministic-only, single-language-only or unsupported-only run skips both
    paid-work denials because it can make no paid call.
13. If model_capable, validate model configuration can supply the possible roles
    and, for translation/continue, at least one distinct A/B pair.
14. Only now capture content snapshots, perform content-dependent planning, call
    a model or mutate a content branch/PR lifecycle. Required label and canonical
    comment mutations remain the earlier explicit exceptions.

The RECEIVED row and bound label event ID are committed before label removal.
**LABEL_REMOVED** is recorded after step 4, **LOCK_ACQUIRED** after step 10 and
**GATES_PASSED** only after step 13. A duplicate delivery never repeats an already
completed phase; it performs
only the next incomplete idempotent effect recorded for that same run.
The provisional RECEIVED envelope may have a null label event ID only inside the
step-2/step-3 transaction boundary; a domain CommandEvent is emitted only after
binding, and no later phase permits null.

All rejections remove the label, post a Russian terminal report, make no model
call or content/branch mutation and fail the Action. An unresolved-billing denial
is distinct from a spent-budget denial and MUST NOT present unknown cost as zero.
A terminal ALREADY_COMPLETE or NO_SUPPORTED_SCOPE result passes the Action.

The lock key is **repository + source_pr**. For an ordinary PR verify, its own PR
number is the source key. For a translation Draft, lineage lookup supplies the
original source PR. Release is in a finally block. A crashed lock becomes
acquirable only when **expires_at <= now**. Lock stealing records both holder run
IDs. Work on a different source key proceeds independently.

### 24.5.3 Active-run preflight

The top-level GitHub Actions **run-name** is display metadata known when the
**pull_request_target: labeled** workflow run is created. It MUST use only
immutable fields present in that labeled event:

~~~text
ydbdoc-ng event_pr=<P> command=<Command>
~~~

**P** is the number of the PR on which the command label was applied. The
top-level run name MUST NOT contain, encode or be parsed as authoritative
**source_pr** or **lineage_id**. In particular, NG MUST NOT infer lineage from
free-form PR title, body or branch text inside a workflow expression.

After step 6 of §24.5.2 resolves command identity, the run MUST atomically persist
an active-run identity in **ng_runs** containing repository, Actions run ID,
event PR, command, resolved source PR and resolved lineage ID or null. For an
ordinary DOC_VERIFY, **source_pr == event_pr** and lineage is null. For
DOC_TRANSLATE, **source_pr == event_pr**; lineage may remain null until the new
lineage is created. For DOC_CONTINUE and DOC_VERIFY on a recognized NG Draft,
source PR and lineage are the exact values resolved from retained lineage or the
strictly parsed Draft marker under the normal lineage rules. A display title is
never lineage evidence.

All pages of queued or in-progress runs for the three NG workflows MUST still be
queried. The current Actions run ID is excluded. Every other Actions run ID is
joined to its persisted **ng_runs** identity. A resolved candidate conflicts when
its source PR equals the current resolved source PR or when both non-null lineage
IDs are equal. If two matching runs observe each other, only the later tuple
**(Actions created_at, Actions run ID)** is rejected; the earlier run is allowed
to continue, so the preflight cannot reject both runs symmetrically.

A queued candidate may not yet have a persisted resolved identity. If its event
PR equals the current event PR, it is a provisional conflict and the same ordering
rule applies. Otherwise NG MUST NOT guess its source PR or lineage from the run
name. The per-source compare-and-set lock in step 10 remains the authoritative
race-safe guard for unresolved candidates and for two runs resolving
concurrently. No local queue is created.

A preflight or lock rejection MUST show the conflicting command, event PR,
resolved source PR and lineage when known, creation/start time and exact GitHub
Actions HTML URL. After identity resolution, the same event PR, source PR,
lineage, command, Actions run ID and workflow URL MUST be written to the job
summary and to the next canonical acknowledgement or terminal report. The
workflow's top-level display name is not the audit record.

### 24.5.4 Terminal mapping

- Verdict PASS yields outcome PASS and Action success.
- Verdict PASS_WITH_WARNINGS yields the same-named outcome and Action success.
- Any quality, safety or operator blocker yields outcome BLOCKED and Action
  failure, even when a red Draft contains independent safe bundles.
- ACL, budget, concurrency, state, location or count denial yields REJECTED and
  Action failure.
- Provider, persistence, snapshot, pagination, critic or GitHub mutation failure
  yields TECHNICAL_FAILURE and Action failure.
- An already merged translation yields ALREADY_COMPLETE and Action success.
- Only unsupported or single-language scope yields NO_SUPPORTED_SCOPE and Action
  success.

### 24.5.5 Allowed run transitions

The only normal phase path is:

~~~text
RECEIVED -> LABEL_REMOVED -> LOCK_ACQUIRED -> GATES_PASSED
-> SNAPSHOT_CAPTURED -> PLANNED -> MODEL_RUNNING -> VERIFIED
-> PUBLICATION_STARTED -> PUBLISHED -> REPORTED -> TERMINAL
~~~

MODEL_RUNNING is skipped when planning needs no model. PUBLICATION_STARTED and
PUBLISHED are skipped when the command is verify or safe diff is empty.

A rejection before lock acquisition follows **LABEL_REMOVED -> REPORTED ->
TERMINAL**. A budget or model-configuration rejection after lock acquisition
follows **LOCK_ACQUIRED -> REPORTED -> TERMINAL** and releases the lock. A
planned no-op follows **PLANNED -> REPORTED -> TERMINAL**. A failure after any
other phase may transition only to **REPORTED -> TERMINAL** after recovery has
made the mutation journal consistent. Phase never moves backward; duplicate
delivery resumes the recorded phase.

Allowed lineage transitions are:

~~~text
none -> WAITING_NO_DRAFT | DRAFT_OPEN
WAITING_NO_DRAFT -> DRAFT_OPEN | REPLACED | EXPIRED
DRAFT_OPEN -> READY_BLOCKED | DRAFT_CLOSED | DAMAGED | MERGED | EXPIRED | REPLACED
READY_BLOCKED -> DRAFT_OPEN | DRAFT_CLOSED | MERGED | EXPIRED | REPLACED
DRAFT_CLOSED -> REPLACED
DAMAGED -> REPLACED
EXPIRED -> REPLACED
MERGED -> MERGED
REPLACED -> REPLACED
~~~

Only observed human/GitHub state may cause DRAFT_OPEN/READY_BLOCKED/CLOSED/DAMAGED/
MERGED transitions. Only TTL evaluation causes EXPIRED. Only an accepted clean
DOC_TRANSLATE causes REPLACED. MERGED and REPLACED lineages never become active
again.

## 24.6 Command algorithms

### 24.6.1 DOC_TRANSLATE

After the universal gates, DOC_TRANSLATE MUST execute:

1. Use the source PullIdentity fetched for the merged-only lifecycle gate. If it
   was not merged, that gate has already rejected before budget, manifest,
   snapshot and model work.
2. Use the SourceManifest already constructed by universal preflight step 11,
   which required all three SHAs, stable identity and complete pagination. Do not
   fetch or reinterpret a second manifest after budget admission.
3. Reconcile the lineage and translation PR records already discovered by the
   lifecycle gate with GitHub once more under the lock. If a translation became
   merged, mark lineage MERGED and stop ALREADY_COMPLETE. If duplicates appeared,
   reject before destructive work. This closes the race without moving either
   normal result behind budget/model calls.
4. The previous terminal and duplicate checks are complete; do not plan content
   for either result.
5. Identify at most one unfinished lineage eligible for clean restart.
6. If an unfinished lineage exists, perform a clean restart only after all gates:
   comment on and close its old Draft if present, delete the remote deterministic
   branch if present, mark the old lineage REPLACED, and leave old artifacts to
   their normal 14-day expiry.
7. Allocate a provisional new lineage ID and reset continue count, decisions,
   force flags and placeholder numbering. Reserve the next repository model pair
   and store its exact A/B identifiers plus ring index; a clean restart does not
   reuse the replaced lineage's pair. Branch is exactly **ydbdoc-review/pr-N**.
8. Capture main SHA once, persist SnapshotRef, and materialize every required blob
   from that SHA. The source PR selects operations only; no source content is
   read from its head or merge commit.
9. Expand and classify manifest operations, apply single-language and supported
   file rules, plan root bundles and dependency closures.
10. Apply stored decisions, which are empty in a new lineage, then deterministic
    gates and pair authority classifiers.
11. If every supported pair is a semantic no-op or waiting for an authority
    decision, persist WAITING_NO_DRAFT, post the path-specific source report and
    stop. A semantic no-op lineage is retained and continue-capable.
12. Generate all candidate bundles, execute shared verification and bounded repair,
    and compute the safe overlay.
13. If safe overlay diff against current-main snapshot is empty, create no branch
    or Draft. Persist **WAITING_NO_DRAFT** and an active lineage only when §23
    supplies a continue-resolvable force, authority, mapping, depth or repair
    question with a valid ready instruction. A semantic no-op always satisfies
    this condition. When there is no continue-resolvable question, retain the run
    artifacts for audit but do not create **ng_active_lineages** or a compact
    LineageSnapshot; terminate with the applicable no-translation, unsupported,
    superseded or technical report. A later DOC_TRANSLATE is therefore a new run,
    not a destructive restart of an unfinished lineage.
14. For a non-empty safe overlay, create a local publication tree from the exact
    main SHA, apply only the safe overlay, make one deterministic bot commit, push
    the branch, and create a new Draft PR. The base branch is main.
15. Post or update the source lifecycle comment and Draft QA comment with the same
    run ID. Set lineage DRAFT_OPEN and its translation PR number.
16. Persist final report, case and call records. Persist the compact lineage and
    set its expires_at to exactly completion time plus 14 days only for
    **WAITING_NO_DRAFT** or **DRAFT_OPEN**. Run artifacts always retain their own
    14-day expiry.
17. Release the lock and map final verdict to Action status.

If safe bundles exist but other bundles are blocked, step 14 still creates a red
Draft containing only safe bundles. If no safe diff exists, no Draft is created.

### 24.6.2 DOC_CONTINUE

After universal gates, DOC_CONTINUE MUST:

1. Resolve exactly one non-terminal lineage from the labeled PR.
2. Accept the source PR as command location only while translation_pr is null.
   Once a Draft exists, accept only that Draft. A wrong-location command rejects
   before snapshot, models or branch work and links to the correct PR.
3. Reject expired, merged, replaced, closed, damaged, duplicate-Draft or
   READY_BLOCKED lineage. A Ready PR report instructs the human to convert it back
   to Draft.
4. Re-read and revalidate that the preselected comment is still the latest
   applicable unconsumed continue comment, with the same comment ID, author,
   created_at and body digest, immediately before parsing it. If its body changed,
   restart selection and ACL for the new immutable body; never parse bytes other
   than the bytes whose digest and author are accepted in step 8.
5. Parse every ready-command decision deterministically. A pathless bilingual
   force/authority choice is accepted only when exactly one bilingual pair waits.
   A pathless one-locale force choice is accepted only when exactly one pair in
   the lineage waits for any force decision.
6. If the instruction is ambiguous, references an unknown path, conflicts within
   the same comment or cannot answer the identified question, reject it without
   consuming the comment or a continue attempt. Repeat exact path-specific
   commands.
7. Reject when continue_count is already 3.
8. In one transaction, append the normalized decisions and raw
   GENERAL_GUIDANCE, mark the comment consumed, and increment continue_count.
   This is the point at which the attempt is consumed.
9. Capture a new immutable latest-main snapshot. Reuse only the original
   SourceManifest and original per-pair initial directions, then re-evaluate
   current applicability and replay every accumulated decision.
10. Rebuild all bundles from zero. Existing translation branch bytes, commits and
    manual changes are never input.
11. Advance model rotation by one valid distinct pair, generate, verify, repair
    and select safe bundles exactly as translate.
12. If no Draft existed and a safe diff now exists, create the deterministic
    branch and first Draft.
13. If a Draft exists, create the full new publication tree from the new main SHA
    and force-update the existing branch with force-with-lease against the remote
    SHA read immediately before push. A lease failure is technical failure and
    MUST NOT be retried as unconditional force.
14. If rebuild has no safe diff, do not delete an existing Draft. Force-update it
    only when the correct rebuilt tree differs; otherwise leave its bytes
    unchanged and report the blockers. The lineage remains continue-capable when
    attempts remain.
15. Update canonical comments, persist the new compact lineage snapshot and set
    expires_at to completion time plus 14 days even when final verdict is red.

For translate and continue, **completion time** is one persisted UTC instant
captured after content publication/verification and before final metadata/report
mutations. The same instant derives lineage expires_at, run finished_at and the
Draft body marker. Updating that marker uses the mutation checkpoint in §24.12.4;
a crash cannot leave a successful accepted continue with a refreshed YDB expiry
but a stale visible expiry marker.

The raw natural-language instruction MAY guide translation wording but MUST NOT
override any deterministic safety, ACL, budget, manifest, direction, single-language,
SUPERSEDED, dependency-count, redirect or continue-limit rule.

### 24.6.3 DOC_VERIFY

After universal gates, DOC_VERIFY MUST:

1. Require the checked PR to be open. Ready and Draft are both allowed.
2. Use the stable PullIdentity and fully paginated PR-files list captured by
   universal preflight step 11. Revalidate head/base identity immediately before
   materializing actual head bytes plus base bytes by those exact SHAs; retry the
   identity-plus-pagination preflight once on contradiction, then fail
   technically. For ordinary PR scope, that API path/status list is authoritative;
   local diff cannot add, remove or reclassify paths. A rename is DELETE old plus
   ADD new for scope identity.
3. If it is an NG Draft, load its unexpired lineage and verify against the exact
   recorded source manifest, source snapshots, paths, directions and decisions.
   Expired lineage produces BLOCKED without a critic call.
4. If it is an ordinary PR, derive canonical pairs only from that PR's changed
   files. Both-locale changes are checked for equivalence with no authority. A
   one-locale change yields MISSING_LOCALE_TRANSLATION, except single-language
   paths. Only local dependencies enter scope.
5. Build VerificationCase from the actual PR bytes. Candidate overlay for an
   ordinary PR represents exactly the API-scoped base-to-head PR result; no
   hypothetical repair changes it. For an NG Draft, reconstruct the overlay over
   its recorded main snapshot and require its materialized bytes to equal the
   actual head. Manual byte changes therefore change the case rather than being
   hidden by retained candidate artifacts.
6. Run deterministic validators. If case hash matches a retained final internal
   verification result, reuse the complete stored critic response and interpreted
   result. Otherwise run the complete critic scope.
7. Never run a repair call. Suggested repairs are report text only.
8. Post or update the canonical QA comment, including on adapter or critic
   failure. For a recognized NG Draft, also update its source lifecycle comment
   to the same run ID, checked Draft link/SHA and result, preserving the two
   cross-links. An ordinary or legacy PR updates only its own QA comment.
9. Never create, close or delete a branch or PR; never commit, push, apply a
   repair, change Draft state or merge.
10. Release the lock and fail only for BLOCKED, REJECTED or TECHNICAL_FAILURE.

### 24.6.4 Canonical continue decision forms

Reports MUST generate these one-line forms. The deterministic parser MUST accept
them case-insensitively for the Russian words, with arbitrary repeated whitespace,
while paths, hrefs, anchors and URLs remain exact:

**<exact-source-path>**, **<exact-ru-path>**, **<exact-en-path>** and
**<exact-root-path>** are full normalized repository paths. **<exact-pair-path>**
is the exact locale-free **CanonicalPairKey.relative_path** (for example
**core/a.md**), so it cannot accidentally encode a conflicting locale choice.

~~~text
/ydbdoc continue всё равно переводи <exact-source-path>
/ydbdoc continue всё равно переводи с русского <exact-ru-path>
/ydbdoc continue всё равно переводи с английского <exact-en-path>
/ydbdoc continue используй русское изображение <exact-ru-path>
/ydbdoc continue используй английское изображение <exact-en-path>
/ydbdoc continue используй русский вариант зависимости <exact-pair-path>
/ydbdoc continue используй английский вариант зависимости <exact-pair-path>
/ydbdoc continue разреши глубину <positive-integer> для <exact-root-path>
/ydbdoc continue используй <absolute-https-url> вместо <exact-original-url>
/ydbdoc continue направь <exact-old-href> на <exact-new-href>
/ydbdoc continue для <exact-pair-path> удали обе стороны
/ydbdoc continue для <exact-pair-path> используй русский файл
/ydbdoc continue для <exact-pair-path> используй английский файл
/ydbdoc continue для термина <exact-anchor> используй русский вариант
/ydbdoc continue для термина <exact-anchor> используй английский вариант
/ydbdoc continue сопоставь термин <exact-ru-anchor> с <exact-en-anchor>
/ydbdoc continue помести <exact-href> в <exact-toc-path> под <exact-parent-href>
/ydbdoc continue повтори классификацию для <exact-source-path>
~~~

One comment MAY contain several such lines plus free prose. Each canonical line
becomes one scoped OperatorDecision. Remaining prose becomes GENERAL_GUIDANCE.
A **повтори классификацию** line becomes RETRY_CLASSIFICATION and is valid only
for the exact pair currently blocked by classifier exhaustion; the rebuild runs
the configured classifier chain again and the decision does not imply authority.
The **для <exact-pair-path> используй ... файл** forms answer either a current
BILINGUAL_AUTHORITY_AMBIGUOUS question or the corresponding valid side of a
MIXED_PAIR_AMBIGUOUS question. For bilingual NO_TRANSLATION, only the explicit
**всё равно переводи с русского/английского <exact-locale-path>** forms are used;
they persist both PAIR_AUTHORITY and FORCE_TRANSLATION. The parser derives the
decision kind from the one outstanding question at that exact scope and MUST NOT
apply a syntactically valid form to a different question kind.
A canonical line with unknown/duplicate scope, invalid URL/integer or conflicting
value makes the complete comment ambiguous and rejects it without consuming the
attempt. A pathless force phrase is normalized only under §24.6.2 step 5.

## 24.7 Manifest, snapshot and pair classification

### 24.7.1 Official manifest construction

The GitHub adapter MUST use GET pull request followed by fully paginated GET pull
request files with per_page 100. It MUST follow pagination until GitHub indicates
there is no next page and MUST record page count. An empty intermediate page,
repeated page, duplicate ordinal/path record, response type mismatch, API error
or page cap without a proved final page is incomplete pagination and blocks.

Accepted file statuses are added, modified, removed and renamed. Unknown status
blocks the manifest. RENAMED requires previous_filename. The identity response
must be re-read after file pagination; a changed base SHA, head SHA,
merge_commit_sha or merged state makes the read contradictory and the whole
manifest is retried once from the beginning without a model call. A second
contradiction blocks.

Local git diff MAY compare the API result and report a diagnostic. It MUST NOT add,
remove, reclassify or reorder an operation.

### 24.7.2 Snapshot rules

Current-main capture is:

1. GET the main ref and record commit SHA;
2. GET its commit/tree identity and persist SnapshotRef;
3. read every blob by that exact commit SHA;
4. verify returned object path, size and digest;
5. never recapture main during the same run.

The base version for a directly changed TOC or glossary structural comparison is
read at SourceManifest.base_sha. Its applicable source version is read from the
current-main snapshot. All translated content comes only from current main.

The exact shared redirect-registry delta attributed to the source PR is the one
narrow provenance exception: when the manifest contains
**ydb/docs/redirects.yaml**, parse that file at **base_sha** and
**merge_commit_sha** and compare exact normalized entries. This read is used only
to prove which mapping the source PR added, modified or deleted; it is never
translated or copied into a candidate. An added mapping is usable evidence only
if the identical mapping still exists in current main. A later-main-only mapping
must not be attributed to the source PR. Missing, unparsable or contradictory
provenance bytes block redirect evidence before model calls.

### 24.7.3 Operation expansion and ordering

Manifest entries are processed in ordinal order. A rename becomes:

~~~text
(ordinal, 0, DELETE previous_filename)
(ordinal, 1, ADD filename)
~~~

No pairing between those two operations is inferred. An added plus removed pair,
including the exact history of YDB PR 45949, is also DELETE plus ADD. Filename,
text and topic similarity MUST NOT create MOVE.

Operations outside **ydb/docs** have no NG scope. Operations at
**ydb/docs/redirects.yaml** use the redirect-registry rule. Locale paths are
mapped only by replacing the first locale component below **ydb/docs**.

### 24.7.4 Pair classification table

For one canonical pair, classify the source PR operations before planning:

| RU manifest side | EN manifest side | PairClass | Required result |
|---|---|---|---|
| add/update | none | SINGLE_RU_WRITE | RU_TO_EN full overwrite |
| none | add/update | SINGLE_EN_WRITE | EN_TO_RU full overwrite |
| delete | none | SINGLE_RU_DELETE | delete EN mirror atomically |
| none | delete | SINGLE_EN_DELETE | delete RU mirror atomically |
| add/update | add/update | BILINGUAL_WRITE | complete-file classifier |
| delete | delete | BILINGUAL_DELETE | no translation change |
| delete | add/update | DELETE_RU_WRITE_EN | operator chooses both delete or EN authority |
| add/update | delete | DELETE_EN_WRITE_RU | operator chooses both delete or RU authority |

Multiple non-equivalent operations on the same locale side are contradictory and
block. RENAME-generated operations are independent and may therefore contribute
to different canonical pairs.

The table supplies manifest shape. Artifact policy then applies the explicit
exceptions from §23: image BILINGUAL_WRITE uses hash/authority without a text
classifier; directly changed bilingual glossary uses entry-level harmonization;
recognized TOC uses scoped structural operations after direction selection; the
shared redirect registry has no locale pair.

Before classification, matching **single_language_patterns** yields
SINGLE_LANGUAGE. A directly changed ineligible type yields UNSUPPORTED.

### 24.7.5 Current applicability and SUPERSEDED

- An original add/update is applicable only if its selected source path exists in
  current main. Otherwise it is SUPERSEDED and creates nothing.
- An explicit delete is applicable only if the deleted source path remains absent
  in current main. Recreation makes it SUPERSEDED.
- A BILINGUAL_WRITE is applicable only if both current paths exist. If either is
  missing, the pair as a whole is SUPERSEDED and the survivor is not reclassified.
- BILINGUAL_DELETE creates no translation change. Recreated later content is
  reported as later state and is not removed by the historical delete.
- Mixed delete/write first applies the same existence tests. If still applicable,
  it remains an operator question.

SUPERSEDED never restores historical bytes, deletes the surviving mirror or
repairs residual TOC/orphan state.

### 24.7.6 Full-file authority

For applicable add/update:

- only one locale changed: that locale is authoritative;
- both changed: classifier reads complete current RU and EN bytes and returns
  exactly NO_TRANSLATION, RU_AUTHORITY, EN_AUTHORITY or AMBIGUOUS;
- selected Markdown/YFM source is translated completely and target bytes are
  replaced completely;
- current target prose and target-only content have no preservation claim.

NO_TRANSLATION and AMBIGUOUS create a retained no-Draft lineage if no safe diff
exists. A force decision is exact-pair scoped and persists through every later
rebuild in that lineage.

## 24.8 Scope, dependencies, files and atomic bundles

### 24.8.1 Eligible files and fixed paths

The only locale roots are **ydb/docs/ru** and **ydb/docs/en**. Recognized TOC
filenames are **toc.yaml**, **toc_p.yaml** and **toc_i.yaml**. The glossary pair
is fixed at:

~~~text
ydb/docs/ru/core/concepts/glossary.md
ydb/docs/en/core/concepts/glossary.md
~~~

The shared registry is exactly **ydb/docs/redirects.yaml**. Images are
**.png .jpg .jpeg .gif .webp .svg**. Companion allowlist is:

~~~text
.yaml .yml .json .txt
.c .cc .cpp .cxx .h .hh .hpp .hxx .inc
~~~

Markdown/YFM documents, recognized TOCs, reached allowlisted dependencies and the
exact registry are eligible. Every manifest entry outside these categories is
listed as unsupported and unchanged. A directly changed unsupported file under a
locale root is a yellow item with the required Russian message; a file outside
the documentation/locale scope is an informational unchanged item. If unsupported
items are the only scope, no Draft is created and Action passes. The same type
reached as a mandatory dependency is red and omits its bundle.

### 24.8.2 Single-language filter

The initial manifest is exactly:

~~~yaml
single_language_patterns:
  - public-materials/*
~~~

Matching is against locale-relative POSIX paths and includes the complete subtree.
It is applied before pair, delete, dependency, content or link logic. A matching
file is SKIPPED: single_language. NG MUST NOT read its bytes for content checks,
follow references, require a mirror, inspect its TOC entry or emit a warning for
a link into it. Only the total skipped count MAY appear.

### 24.8.3 Dependency traversal algorithm

For every root article independently:

1. Initialize a FIFO queue with root path at depth 0 and initialize the canonical
   visited set with that root path. Root does not consume file allowance and an
   edge returning to it is recorded as a cycle/repeat edge, never enqueued or
   counted as a dependency.
2. Parse Markdown/YFM with the approved AST. Do not regex-scan raw text.
3. Emit edges, in source order, only for parsed locale-local include nodes, parsed
   image nodes and parsed ordinary local links whose final extension is in the
   companion allowlist.
4. Resolve against the containing file, normalize and reject locale escape,
   repository escape, symlink or missing mandatory blob as red.
5. An ordinary link to another Markdown article emits no edge.
6. A Markdown documentation include may recursively emit the same three edge
   kinds.
7. YAML, JSON, TXT, C/C++ companions and images are leaves.
8. TOCs use navigation scope and emit no article dependency edge.
9. Before enqueue, compute next_depth = parent depth + 1. If it exceeds the
   root's effective depth, record the complete chain and do not enqueue.
10. If canonical path was already visited, record a cycle/repeat edge and do not
    enqueue. Cycles are not errors.
11. Count each unique dependency once per root. If count would exceed
    **YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE**, stop planning that root and block
    it. There is no partial closure.

The default effective depth is 3. DEPTH_LIMIT decisions store one integer
max_depth for one exact root article and apply to its complete closure. Overflow
reports default, required depth and chain, with a ready command. File-count limit
is exactly 100 by confirmed configuration and cannot be overridden.

A companion directly changed by the source PR is a standalone depth-0 root and
does not consume another root's allowance unless also reached there. A shared
dependency counts independently in each root closure.

### 24.8.4 Images

Directional document bundles copy current authoritative image bytes over the
target byte-for-byte. Report source, target, byte size and SHA-256; verifier
requires byte and digest equality. No OCR or model call is allowed.

For a standalone image pair:

- exactly one changed locale is authoritative;
- both changed and equal hashes is no-op;
- both changed and unequal hashes is an operator authority question;
- semantic no-op and force-translation decisions do not affect it.

A directional root owns a reached image over a conflicting direct edit of the
other locale and the report names the overwritten edit. Opposite-direction roots
requiring one differing image pair block all dependent bundles until an exact
image-authority decision.

### 24.8.5 Companion and code policy

Markdown/YFM pages and includes are fully translated both directions. For RU_TO_EN
companions:

- YAML/YML: translate comments and scalar values whose exact key is title or
  description; preserve every other key and value;
- JSON: translate Cyrillic string values, never keys;
- TXT: translate all natural-language Cyrillic text;
- C/C++: translate comments and clearly user-facing string literals, never code,
  identifiers, paths, URLs, placeholders or technical literals.

For EN_TO_RU every non-Markdown companion is copied as-is. Markdown front matter
is documentation: comments, title and description follow document direction in
both directions. Invalid companion syntax is red. Residual Cyrillic is red only
where its RU_TO_EN policy requires translation. Unknown mandatory type is red.

Inside Markdown fences, boundary, language, commands, code, identifiers and
technical values are exact. RU_TO_EN translates Cyrillic comments and only
clearly user-facing strings. Ambiguous strings and example/SQL data remain
unchanged with a yellow exact-location warning. EN_TO_RU fence content stays
English. Every verification pass checks fence structure and technical tokens.

An explicit source delete of Markdown, image or companion is a standalone root.
If applicable, target mirror deletion occurs. Markdown deletion additionally
requires exact target TOC href removal and redirect. Image and companion deletion
remove only target mirror. A target mirror already absent is a no-op. Reference
checking is limited to other scoped closures.

### 24.8.6 Bundle construction and conflicts

A document-root bundle contains target document, mandatory translated includes,
required images/companions, mandatory references to opportunistic used-glossary
entry bundles, minimal TOC edits and all deletes/redirects belonging to the root.
A standalone TOC, image, companion or explicit delete has its own bundle. Each
selected glossary entry pair is a separate GLOSSARY bundle identified by stable
anchor or resolved anchorless interval identity and carries GlossaryEdit values,
not a competing full-file Write.

Shared dependency rules:

1. Before model calls, a canonical textual dependency required by more than one
   root in the same direction is lifted into one shared dependency bundle. It is
   translated, criticized and repaired exactly once from the current source
   bytes; every requiring root lists that bundle in mandatory_bundle_ids. A
   non-textual same-direction shared dependency is likewise represented once and
   copied deterministically. NG MUST NOT ask independent root prompts to generate
   competing translations of the same shared target path.
2. Opposite directions first compare current locale content for equivalence.
3. Equivalent dependency content requires no overwrite and no conflict.
4. Different content blocks every requiring bundle, shows both chains and waits
   for an exact authority decision.
5. Unsafe shared dependency blocks every requiring bundle.

A safe shared dependency is included in the publication overlay only through the
transitive closure of at least one selected safe root. If every requiring root is
omitted, the otherwise-safe dependency is not published by itself.

Publication is closed over mandatory_bundle_ids. No page without mandatory
include, TOC href without page, deletion without TOC update/redirect, or half
bundle may publish.

Before verification, intent composition is deterministic per target path:

1. Direct Write/Delete conflicts use the model invariants above.
2. TocEdits are applied to one parsed snapshot tree in stable bundle-manifest and
   operation order. Each **before** precondition must match the same node identity
   at application time. Independent edits to different nodes compose; incompatible
   edits to one node/position block every contributing bundle instead of using
   last-writer-wins.
3. RedirectAppends are keyed by exact old href. Identical old/new pairs deduplicate;
   two destinations for one old href block both. Non-conflicting appends are
   serialized once over the exact registry snapshot.
4. GlossaryEdits use the dedicated entry composer in §24.10.9.
5. The composer emits at most one final Write or Delete per path into
   CandidateOverlay. Whole-overlay validation repeats all preconditions after
   unsafe bundles and their intents are removed.

## 24.9 Translation, classification, rotation and repair state machines

### 24.9.1 Deterministic model rotation

Configuration supplies ordered **translator_rotation** and **critic_rotation**
lists of fully qualified provider/model identifiers. Build the pair ring in this
exact order:

~~~text
[(a, b) for a in translator_rotation
        for b in critic_rotation
        if a != b]
~~~

An empty ring is a red independent-verification failure before translation calls.
Configuration SHOULD place different model families in the two rotations, but
exact identifier inequality is the mandatory gate.
New DOC_TRANSLATE reserves the next repository rotation counter atomically.
DOC_CONTINUE uses **previous model_rotation_index + 1 modulo ring size**. The
selected A/B pair is fixed for the run. A fallback may be a third identifier but
MUST NOT make the effective translator/critic identifiers equal in that
operation. All identifiers are included in the case hash.

DOC_VERIFY on an NG Draft uses the lineage's fixed B identifier. An ordinary
DOC_VERIFY uses the first configured critic identifier and does not advance the
translation rotation counter. Verify never selects a translator or repairer.

Classifier fallback chain is ordered configuration, not the A/B ring. Each
classifier operation starts at its first identifier. A valid AMBIGUOUS verdict is
terminal product output and does not advance to another classifier.

### 24.9.2 Model work units

Call counts are defined per immutable unit:

- one **semantic-no-op classification unit** per applicable one-direction
  document or standalone translatable companion root. Mandatory dependency
  members, including lifted shared dependencies, are visible in their root's
  classification input and do not create extra semantic-no-op classifiers;
- one **pair-authority classification unit** per applicable BILINGUAL_WRITE
  Markdown/YFM, companion or TOC pair. Image pairs use hashes instead. A directly
  changed bilingual glossary creates one **glossary-entry authority
  classification unit** for each deterministically paired changed entry whose
  current definitions differ; it does not create one whole-file authority call.
  One-locale direct TOC structural operations and mandatory glossary
  synchronization never enter semantic-no-op classification, because that filter
  cannot suppress their explicit §23 operations;
- one **translation unit** per generated textual operation bundle. Its prompt
  includes the complete root and all non-lifted textual mandatory members. A
  lifted same-direction shared textual dependency is its own one translation
  unit and is never regenerated inside a requiring root prompt, so there is no
  hidden per-segment or per-requirer paid call;
- one **critic unit** per candidate operation bundle containing translated
  Markdown/YFM, translatable companion text, glossary meaning or a translated TOC
  label. Image-only, byte-copy-only and deterministic delete/TOC/redirect bundles
  have zero critic units and use deterministic checks only. Every verification
  pass reruns all critic units unless the complete case hash is reused;
- one **repair unit** per critic unit having at least one MODEL_REPAIRABLE red
  issue.

Segmentation inside one request is deterministic preparation only and MUST NOT
create extra model calls. If a complete unit exceeds a provider context limit,
that bundle is omitted as red MODEL_INPUT_TOO_LARGE. NG v1 MUST NOT silently
split it into an unbounded call count.

**operation_id** is the SHA-256 of unit kind plus its canonical pair/root/entry
scope and stable manifest order. It is unchanged on delivery recovery. Call
indices are exact: classifier and initial translator use pass_index 0; critic
passes use 1, 2 and 3; repairs use pass_index 1 and 2. attempt_index 0 is primary,
1 is the translator/repair fallback or the critic format-repair/fallback call,
and critic attempt_index 2 is the fallback after a format-repair failure. The
resulting operation_id/role/pass_index/attempt_index tuple is unique in one run
and deterministically derives call_id.

Before invoking any state-machine arrow that calls a provider, NG MUST:

1. compare-and-set one **RESERVED** ModelAttemptRecord for the deterministic
   call_id, including request digest, provider/model, reserved_at and
   reservation_moscow_day;
2. invoke the provider adapter for that call_id at most once;
3. compare-and-set the complete typed result, returned usage/cost and response
   artifact as **RESULT_RECORDED** before parsing or using the result.

A resumed **RESERVED** call is never dispatched again. Recovery first records
**UNKNOWN_BILLED**, stops the current unit and all later paid units, and produces
TECHNICAL_FAILURE. If the provider exposes authoritative exact-request lookup,
reconciliation MAY subsequently replace UNKNOWN_BILLED with RESULT_RECORDED or
RECONCILED_NOT_BILLED. Inconclusive lookup, a provider without that capability,
local logs, operator text and cost estimates cannot resolve the state. A resolved
original result is consumed on delivery recovery without a second provider call;
a proven-not-billed attempt follows its already selected state-machine failure
edge. This durability protocol wraps classifier, translator, critic, format
repair and repair calls identically.

### 24.9.3 Classifier state machine

For each classification unit, including one exact glossary-entry unit, call each
configured classifier identifier at most
once, in order, until a valid schema result:

~~~text
START
  -> valid verdict: TERMINAL_VALID
  -> timeout/provider error/malformed: NEXT_IDENTIFIER
  -> no identifier left: EXHAUSTED
~~~

Thus call count is exactly **1..K** for a chain of K identifiers, or zero when a
stored exact-pair force/authority decision skips classification.

- A one-direction classifier returns SemanticNoopVerdict. TRANSLATION_REQUIRED
  and UNCERTAIN stop the chain and proceed with the already known direction.
- One-direction EXHAUSTED is fail-open: proceed with known direction.
- Bilingual EXHAUSTED is red, creates no candidate for the pair and asks the
  operator to retry through continue.
- One-direction valid NO_TRANSLATION creates a force-capable no-Draft lineage.
- Bilingual valid NO_TRANSLATION presents RU and EN path-specific force choices.
- A valid force decision stores both exact authority and force_translation=true;
  later rebuilds skip the negative verdict for that pair.

### 24.9.4 Initial translation state machine

Each translation unit has:

~~~text
selected A attempt
  -> success: CANDIDATE
  -> technical failure: one eligible translator fallback attempt
       -> success: CANDIDATE
       -> failure: OMIT_BUNDLE_RED
~~~

There are exactly one or two attempts. Malformed translation counts as technical
failure and does not get a separate format-repair call. The fallback must differ
from the fixed critic B. No eligible distinct fallback means one attempt total.

### 24.9.5 Critic attempt sequence

For each critic unit in each verification pass:

1. Call the pass primary critic once.
2. If and only if the response is malformed structured output, call the same
   identifier once with the format-repair prompt.
3. If there is still no valid verdict, call one eligible fallback critic once.
4. If still invalid, emit CRITIC_UNAVAILABLE and no invented content defect.

Therefore a critic unit uses exactly:

- 1 call on primary valid output;
- 2 calls on primary malformed then repaired format;
- 2 calls on primary technical failure then fallback;
- 3 calls on malformed plus failed format repair plus fallback.

No further fallback or retry is allowed. The fallback effective identifier must
remain distinct from the opposite fixed model role.

### 24.9.6 Two-repair loop

For every translated candidate the role sequence is fixed:

~~~text
initial:       TRANSLATOR_A
verify pass 1: CRITIC_B
repair 1:      REPAIR_B, only if repairable red exists
verify pass 2: CRITIC_A, fresh context
repair 2:      REPAIR_A, only if repairable red exists
verify pass 3: FINAL_CRITIC_B
~~~

If pass 1 has no repairable red, the candidate is terminal after pass 1. If pass 1
has repairable red, repair 1 is consumed even when both primary and fallback
repair calls fail; candidate remains unchanged and pass 2 still runs. If pass 2
has no repairable red, the candidate is terminal after pass 2. Otherwise repair 2
is consumed and pass 3 always runs, even when both repair calls failed. Pass 3 is
always terminal. Operator-required blockers are retained at every stopping point.

Each repair unit calls its selected repair model once and one eligible fallback
once only after technical failure. It therefore uses exactly one or two calls.
Repair prompts contain only reported repairable issues and exact source material.
Operator-required issues coexist but are not passed as repair instructions.

Maximum attempts per translated critic unit after classification are:

~~~text
initial translation: 2
critic passes:        3 passes * 3 = 9
repair passes:        2 passes * 2 = 4
maximum total:        15
~~~

The normal green path is exactly two calls per translated bundle: one initial
translation and one first critic. Classification adds its separately defined
1..K calls. A case-cache hit in DOC_VERIFY uses zero critic calls but still runs
all deterministic validators.

On a DOC_VERIFY cache miss, there are zero translator, classifier and repair
calls, and exactly one to three critic attempts per critic unit under
§24.9.5. A deterministic-only verify case has zero model calls.

After final pass, any bundle with a remaining BLOCKED issue is omitted. Independent
safe bundles may publish in a red Draft. Every attempt, including failed fallback,
is persisted immediately.

### 24.9.7 Repair eligibility

MODEL_REPAIRABLE red issues are limited to semantic loss/distortion, omitted
prose, incorrect terminology, residual Cyrillic in a required translatable
location, and lost Markdown/placeholder/technical elements whose exact source
content is known.

Unresolved URL, redirect, TOC placement/label, depth permission, direction,
missing source/dependency, unsupported type, ACL, budget, expiry, critic
infrastructure and any authority conflict are OPERATOR_REQUIRED or OPERATIONAL
and MUST NOT consume automatic repair by themselves.

## 24.10 Links, TOC, redirects and glossary

### 24.10.1 URL parsing and classification

Links are parsed from AST nodes. Internal documentation recognition occurs before
external language classification.

Internal means:

- a relative or root-relative path resolving below one locale root; or
- absolute HTTPS host **ydb.tech** with path beginning **/docs/ru/** or
  **/docs/en/**.

For every other absolute URL, collect case-insensitive markers after standard URL
parsing:

- RU: hostname begins **ru.**, complete path segment **ru**, query
  **lang=ru** or **locale=ru**;
- EN: hostname begins **en.**, complete path segment **en**, query
  **lang=en** or **locale=en**.

Only RU markers means Russian, only EN means English, none means neutral, and both
means unresolved. No download or LLM language guess participates.

### 24.10.2 Internal links

RU_TO_EN rewrites a RU internal path only when its EN mirror exists in current
main or the same candidate. With fragment, the complete rewrite occurs only when
the exact fragment exists in the mirrored target. Otherwise retain the complete
working RU URL and emit yellow evidence with file, exact line and recommendation.
No placeholder is used.

EN_TO_RU uses an existing RU mirror. If absent, retain the EN URL without error.
An internal Markdown link never expands dependency scope. Links into a
single-language pattern are preserved without validation or warning.

### 24.10.3 External links and placeholders

RU_TO_EN keeps English and neutral external URLs. For canonical
**ru.wikipedia.org**, call the official API for interlanguage mapping, follow
Wikipedia redirects and accept only an existing canonical EN page. An original
fragment is retained only when that exact fragment exists on the EN page.
Timeout, 429, 5xx, network error, missing mapping or missing fragment is unresolved.

Every other explicitly Russian URL, every RU/EN-marker conflict and unresolved
Wikipedia URL receives a stable placeholder:

~~~text
https://ydbdoc.invalid/NEEDS-EN-URL-NNN
~~~

Number in deterministic manifest/document occurrence order. The lineage stores
exact original URL to placeholder mapping; repeated exact URL reuses it and
continue never renumbers. Report every location and a ready exact replacement
command. Placeholder presence is blocking.

An operator replacement must be an absolute syntactically valid HTTPS URL. It is
applied to every exact original occurrence and is not network-probed. EN_TO_RU
retains English URLs.

### 24.10.4 TOC parse model

A recognized TOC is parsed into an ordered immutable tree:

~~~text
YamlSpan(start_byte: int, end_byte: int, start_line: int, end_line: int)
OpaqueYamlField(key: str, ordinal: int, raw_field_bytes: bytes,
                raw_value_bytes: bytes, span: YamlSpan)

TocNode(
  node_id: str,
  name: str | None,
  href: str | None,
  include_path: str | None,
  service_fields: tuple[OpaqueYamlField, ...],
  children: tuple[TocNode, ...],
  source_location: exact YAML path and line,
  source_span: YamlSpan,
  raw_node_sha256: Digest
)
~~~

Node ID is explicit href when unique, otherwise include_path when unique,
otherwise the stable source structural path. Duplicate href/include identities
remain separate nodes and trigger ambiguity whenever an operation needs one.
**service_fields** contains every key other than the recognized name, href,
include.path and child-items structure, in source order, with arbitrary nested
YAML preserved as opaque bytes. The lossless parser/CST retains comments, anchors,
scalar style, whitespace and ordering for every unchanged target span. An edit to
an existing target TOC patches only the exact recognized spans and insertion/
removal boundary selected by the operation; it MUST NOT parse-render the complete
target file or normalize unrelated bytes. A newly created target TOC may use the
canonical renderer, but it deep-copies all source service-field nodes and their
order while changing only labels and locale-mirrored hrefs. Unsupported YAML
constructs that prevent lossless localized editing block the affected TOC bundle.

### 24.10.5 Direct source TOC delta

The TOC locale pair first follows the complete normal manifest classification
table. SINGLE_RU_WRITE or SINGLE_EN_WRITE supplies the structural source delta
without a semantic-no-op classifier; deterministic structural operations MUST
NOT disappear merely because they contain no translatable label. BILINGUAL_WRITE
receives the complete-current-pair authority classifier; the selected authority
supplies the delta, NO_TRANSLATION is no-op, and AMBIGUOUS waits for an exact
authority decision. BILINGUAL_DELETE is no-op, and a mixed delete/write pair
waits for its normal mixed-pair decision. Even after authority selection, an
existing target TOC is changed only by scoped tree operations and is never fully
overwritten.

For a TOC directly changed by the source PR:

1. Parse its immutable base version and current applicable source-main version.
   For an applicable manifest ADD, an absent base is the empty tree. For an
   applicable manifest DELETE, an absent current source is the empty tree. Any
   other missing or unparsable required version is an exact structural blocker,
   not an inferred empty tree.
2. Compute ordered tree edit operations: addition, removal, move, label change,
   href change and include.path change. Matching uses exact unique href, then
   exact unique include.path, then unchanged structural identity. It never uses
   label similarity.
3. Map paths to the opposite locale and apply only those operations to the target.
4. Leave all unrelated target entries and drift untouched.
5. If exactly one target node/position cannot be identified, omit the affected
   TOC bundle and ask the operator.
6. Assert that every target byte outside the union of exact edited CST spans and
   insertion/removal boundaries is byte-identical to the snapshot. Any unrelated
   byte change is ATOMIC_BUNDLE_INCOMPLETE and blocks the TOC bundle.

A real scoped target change creates a Draft even without documents. A no-op does
not.

### 24.10.6 Missing and existing target TOC

If target TOC is absent and source TOC exists, create it at the locale-mirrored
path, preserve complete hierarchy/order and service fields, translate labels and
mirror hrefs. A source entry outside scope whose target page is absent keeps its
working source-locale href and is yellow. A single-language href is retained
without warning.

If target TOC exists, only minimal edits are allowed. For insertion, select the
exact mirrored ancestor href chain. Place after nearest previous source sibling
already in target; else before nearest following sibling; else at end of the one
unambiguous parent. Multiple TOCs, parents, duplicate hrefs or contradictory
sibling order is operator-required. Its report names the exact TOC, node label or
duplicate href, every candidate parent/position and a ready placement command.

Delete of a recognized source TOC removes only unambiguously mapped target nodes,
preserves target-only nodes/service data and deletes the target file only when the
scoped result is completely empty.

### 24.10.7 Redirect algorithm

Every target TOC href removal has one redirect in the same atomic bundle. Accepted
evidence, in priority order:

1. exact mapping directly added by the source PR in the shared registry;
2. exact old href replaced by exact new href in the same source TOC position;
3. exact operator decision.

No text, filename or topic similarity is evidence. Convert source successor to
the target locale. Destination must exist in current main or be created by the
same safe bundle. Cross-locale destination is forbidden.

If the destination is produced by a distinct ADD root operation, operations
remain classified as independent DELETE and ADD, but the deletion bundle lists
the add bundle in mandatory_bundle_ids. Publication closure then makes the new
target page, redirect, old-page deletion and TOC removal one safety unit. This is
the required PR 45949 shape and does not create a logical MOVE.

For each proposed append:

- identical existing mapping is no-op;
- conflicting existing mapping is operator-required and never overwritten;
- existing chains are not collapsed;
- existing redirects are never changed or removed;
- unresolved destination omits target deletion, TOC removal and redirect
  together.

Direct source-PR append is already in current main, supplies evidence and produces
no standalone change. Direct modification or deletion of an existing registry
entry is red and is neither mirrored nor replayed. NG-generated registry changes
are append-only.

The registry adapter is lossless for existing bytes. It parses exact entry spans,
validates the complete YAML, and serializes new entries with one versioned
canonical append renderer at the registry's append boundary. Every pre-existing
byte, entry order, comment, scalar style and line ending remains identical. If a
valid append cannot be made without rewriting existing bytes, the redirect bundle
is blocked; parse-render normalization is forbidden.

"Direct source-PR" above means the exact base_sha-to-merge_commit_sha registry
delta from §24.7.2, not a base-to-current-main difference and not an entry inferred
from the PR file name alone.

This invariant applies to item, subtree, complete TOC and genuine no-successor
deletion without exception.

### 24.10.8 Glossary parser and identity

An entry begins at heading level three or deeper and ends at the next heading of
same or higher level. Explicit YFM anchor is preferred identity. Duplicate anchor
is red at every exact line. Same anchor plus renamed heading is update; changed
anchor is delete plus add.

Anchorless entries pair only by ordinal position inside an interval bounded by
the same stable neighboring anchors. Multiple insert/delete/reorder making that
mapping non-unique blocks only the affected glossary bundle and requires exact
mapping or interval authority. Title similarity and LLM identity guesses are
forbidden.

### 24.10.9 Glossary scope and harmonization

- Only RU glossary changed: full current RU-to-EN synchronization and full pair
  verification.
- Only EN changed: full current EN-to-RU synchronization and full verification.
- Both changed: entry-level harmonization of only changed entries; safe entries
  may publish independently.
- RU-only entry adds translated EN; EN-only adds translated RU; differing existing
  definitions use cheap authority classification or an operator question.
- Equal entries are unchanged.

Used glossary entries in ordinary bundles are detected only by a parsed link to
explicit anchor or exact visible heading title with Unicode case-fold and
collapsed whitespace. No morphology, stemming, fuzzy match, alias extraction or
LLM expansion.

A directional article fully harmonizes its used entry from the same source
locale. Direct conflicting edit in the other locale is overwritten unless there
is the explicit one-locale full-glossary versus opposite-direction article
conflict or opposite-direction bundles use the same differing term. Those cases
omit the entry and all dependent article bundles until authority is chosen.

Within the glossary scope selected by direct change or deterministic usage, a
term present in only one locale is blocking until its translated counterpart is
safely added. The verifier compares term identity, RU/EN names, definition
meaning, links, placeholders and technical notation for every selected entry.

Glossary publication uses this deterministic composition algorithm:

1. Parse the exact RU and EN baseline files once and assign every selected pair
   its stable entry identity, exact source interval and ordinal. Section intervals
   may overlap under the heading rule; overlapping edits are detected in step 4,
   not silently flattened.
2. Represent each pair as one glossary-entry bundle with zero, one or two
   GlossaryEdit values. Article bundles that use it list that entry bundle in
   **mandatory_bundle_ids**.
3. After verification, discard edits from OMITTED entry bundles. Sort remaining
   edits by glossary path and baseline entry order, then apply them to the exact
   baseline lossless Markdown/YFM CST. **desired_ordinal** fixes insertion and
   reorder position; additions at the same boundary preserve manifest/entry
   order. Bytes outside selected entry spans and exact insertion
   boundaries remain byte-identical, so unrelated glossary drift and presentation
   are not normalized.
4. Two safe edits for the same entry identity, overlapping baseline interval or
   incompatible insertion boundary are CONFLICTING_TARGET_WRITE and neither
   publishes. Disjoint edits compose into exactly one final Write per glossary
   path.
5. Reparse and verify the complete composed RU/EN files and assert unchanged-span
   byte equality before whole-overlay publication. A composition, parse or
   unrelated-byte failure blocks all bundles whose edits entered that composed
   file; it never falls back to last-writer-wins.

One-locale full synchronization is still full in semantic scope, but is planned
as entry bundles plus one reserved **__skeleton__** GlossaryEdit for preamble,
inter-entry structure and other bytes outside entry intervals. Every authoritative
source entry has an ADD or UPDATE edit in authoritative source order; every
target-only entry has a DELETE edit; the skeleton is translated in the same
direction. When all are safe, the composed target is a complete synchronization
of source structure, order and meaning. If the explicit §23
full-glossary/opposite-article conflict omits one entry, that exact target entry
and its target ordinal are preserved while independent entry and skeleton edits
may publish. Both-locale direct harmonization and opportunistic usage do not
select **__skeleton__** or unrelated entries. Whole-file structural failure still
blocks every edit because safe entry identity cannot be established.

Historical unrelated entry drift is at most yellow. Duplicate anchors, unclosed
Markdown/YFM or other whole-file parse failures are red regardless of scoped
entries. Exact glossary snapshots and selected entries are in case metadata.

## 24.11 Shared verification and atomic publication

### 24.11.1 Service boundary

All commands MUST use one **VerificationService.verify(case)**. It has:

- a pure DeterministicEngine that consumes VerificationCase plus materialized
  critic responses and returns VerificationResult;
- a CriticAdapter that performs bounded external calls and returns structured
  response DTOs.

The engine performs no filesystem, git, GitHub, clock, environment, model or
network access. No caller may override prompts, severity, issue interpretation or
verdict.

### 24.11.2 Case hash

case_sha256 is SHA-256 over canonical JSON containing:

- exact source and target path, bytes digest, size and commit SHA;
- manifest and snapshot identities;
- pair operations, directions, closures and candidate overlay;
- pending deletes and all effective operator decisions;
- exact glossary snapshots;
- verifier/case/rule/prompt versions;
- selected primary and fallback model identifiers;
- central single-language manifest and all behavior-affecting configuration.

The source/target commit identities above are the immutable source and baseline
snapshot commits used to interpret the candidate, not the later publication or
checked PR-head commit. **checked_commit_sha**, evidence **commit_sha**,
publication commit metadata, timestamps, run ID, cost and comment IDs are
excluded unless they alter an operator decision. Evidence content digests and all
actual source/target/candidate byte digests remain included. Therefore binding
the exact internally verified bytes to a newly created Draft commit does not
invalidate the case, while any manual byte change does. Any included byte or
behavioral configuration change yields a new hash.

### 24.11.3 Deterministic checks

Every pass checks at least:

1. snapshot and overlay digest integrity;
2. complete expected pair coverage and SUPERSEDED exclusions;
3. Markdown/YFM parse/render structure, heading, anchor, include, fence, tab,
   table, placeholder and technical-token preservation;
4. semantic scope completeness from the critic response;
5. policy-scoped residual Cyrillic;
6. YAML/JSON/C/C++ syntax and protected locations;
7. image byte/hash equality;
8. dependency closure, depth/count, missing targets and direction conflicts;
9. internal path and exact fragment validity under the retained-link rule;
10. unresolved external placeholders and operator URL mappings;
11. minimal TOC structure, target existence, duplicates and parent/sibling rules;
12. append-only, same-locale, existing-destination redirect invariants;
13. glossary identity, selected term set, definitions, links, notation and
    dependent bundle consistency;
14. atomic bundle closure and conflicting writes/deletes.

### 24.11.4 Cache

The cache key is exact case hash. Store final valid structured critic responses
for every critic unit and the interpreted complete report for 14 days.
DOC_VERIFY on an exact hit reruns all deterministic checks and reinterprets the
stored response with the same engine. It MUST NOT reuse a partial bundle/file
result. For an NG Draft, exact-hit comparison reconstructs the base-to-head
candidate from actual PR bytes and the recorded baseline; retained candidate
bytes cannot substitute for this comparison. A fully published internal
candidate can therefore hit after only the publication commit identity changes.
A red partial publication whose actual overlay differs from the internally
checked candidate is a miss and runs the complete critic scope. Any other case
difference also reruns the complete critic scope. The current checked commit and
rendered-report digest are rebound after interpretation and are never copied from
the cached run; the published report is semantically identical for equal cases.

### 24.11.5 Severity and publication

BLOCKED means merge disallowed and Action fails. WARNING-only means
PASS_WITH_WARNINGS and Action succeeds. No warning/blocker means PASS. Yellow
alone never triggers repair.

BLOCKED includes lost/distorted meaning, incomplete translation, broken
structure/link/code/placeholder/TOC, unresolved dependency or operator choice,
residual required Cyrillic, unsafe output and unavailable critic. WARNING is
limited to style, readability, optional wording or punctuation without semantic
loss, plus explicit contract warnings such as retained working RU links and
ambiguous unchanged fence strings. INFO is technical detail and never changes a
verdict.

After final verification:

1. attach every issue to affected bundle IDs;
2. propagate a blocking dependency issue to all requiring bundles;
3. mark a bundle SAFE only when it and all mandatory dependencies have no BLOCKED
   issue;
4. materialize safe overlay from SAFE bundles only;
5. validate overlay again as a whole;
6. publish it atomically as one commit/tree;
7. list every PUBLISHED and OMITTED bundle.

A critic-unavailable translated bundle is unsafe and omitted. Operator blockers
whose affected bundle was never generated remain in the red report while
independent safe bundles publish.

### 24.11.6 Stable issue and operational codes

The first two columns are default severity and issue class. A code MUST NOT be
silently downgraded. INFO codes do not enter VerificationIssue when they are only
run outcomes.

~~~text
ACL_DENIED                         BLOCKED OPERATIONAL
BUDGET_EXHAUSTED                   BLOCKED OPERATIONAL
MODEL_CALL_BILLING_UNKNOWN         BLOCKED OPERATIONAL
CONCURRENT_RUN                     BLOCKED OPERATIONAL
LOCK_HELD                          BLOCKED OPERATIONAL
SOURCE_NOT_MERGED                  BLOCKED OPERATIONAL
VERIFY_PR_NOT_OPEN                 BLOCKED OPERATIONAL
LINEAGE_EXPIRED                    BLOCKED OPERATIONAL
LINEAGE_READY                      BLOCKED OPERATIONAL
LINEAGE_CLOSED                     BLOCKED OPERATIONAL
LINEAGE_DAMAGED                    BLOCKED OPERATIONAL
DUPLICATE_DRAFTS                   BLOCKED OPERATIONAL
CONTINUE_LIMIT                     BLOCKED OPERATIONAL
WRONG_CONTINUE_PR                  BLOCKED OPERATIONAL
INSTRUCTION_AMBIGUOUS              BLOCKED OPERATOR_REQUIRED
MANIFEST_INCOMPLETE                BLOCKED DETERMINISTIC
SNAPSHOT_UNAVAILABLE               BLOCKED DETERMINISTIC
MODEL_PAIR_UNAVAILABLE             BLOCKED OPERATIONAL
MODEL_INPUT_TOO_LARGE              BLOCKED DETERMINISTIC
TRANSLATION_UNAVAILABLE            BLOCKED OPERATIONAL
CRITIC_UNAVAILABLE                 BLOCKED OPERATIONAL
PERSISTENCE_FAILURE                BLOCKED OPERATIONAL
GITHUB_MUTATION_FAILURE            BLOCKED OPERATIONAL
UNSUPPORTED_ROOT_FILE              WARNING OPERATIONAL
UNSUPPORTED_DEPENDENCY             BLOCKED OPERATOR_REQUIRED
DEPENDENCY_DEPTH_EXCEEDED          BLOCKED OPERATOR_REQUIRED
DEPENDENCY_COUNT_EXCEEDED          BLOCKED DETERMINISTIC
DEPENDENCY_MISSING                 BLOCKED OPERATOR_REQUIRED
SHARED_DEPENDENCY_AUTHORITY        BLOCKED OPERATOR_REQUIRED
IMAGE_AUTHORITY_AMBIGUOUS          BLOCKED OPERATOR_REQUIRED
BILINGUAL_AUTHORITY_AMBIGUOUS      BLOCKED OPERATOR_REQUIRED
BILINGUAL_CLASSIFIER_UNAVAILABLE   BLOCKED OPERATIONAL
MIXED_PAIR_AMBIGUOUS               BLOCKED OPERATOR_REQUIRED
SUPERSEDED_OPERATION               INFO    OPERATIONAL
SINGLE_LANGUAGE_SKIPPED            INFO    OPERATIONAL
NO_TRANSLATION_REQUIRED            INFO    OPERATIONAL
EXTERNAL_URL_UNRESOLVED            BLOCKED OPERATOR_REQUIRED
INTERNAL_SOURCE_LINK_RETAINED      WARNING DETERMINISTIC
REDIRECT_UNRESOLVED                BLOCKED OPERATOR_REQUIRED
REDIRECT_CONFLICT                  BLOCKED OPERATOR_REQUIRED
REDIRECT_CROSS_LOCALE              BLOCKED DETERMINISTIC
REDIRECT_MUTATION_FORBIDDEN        BLOCKED DETERMINISTIC
TOC_PARENT_AMBIGUOUS               BLOCKED OPERATOR_REQUIRED
TOC_NODE_AMBIGUOUS                 BLOCKED OPERATOR_REQUIRED
TOC_SOURCE_LINK_RETAINED           WARNING DETERMINISTIC
GLOSSARY_DUPLICATE_ANCHOR          BLOCKED DETERMINISTIC
GLOSSARY_ENTRY_ID_AMBIGUOUS        BLOCKED OPERATOR_REQUIRED
GLOSSARY_AUTHORITY_CONFLICT        BLOCKED OPERATOR_REQUIRED
GLOSSARY_TERM_MISSING              BLOCKED MODEL_REPAIRABLE
GLOSSARY_STRUCTURE_INVALID         BLOCKED DETERMINISTIC
MISSING_LOCALE_TRANSLATION         BLOCKED DETERMINISTIC
SEMANTIC_LOSS                      BLOCKED MODEL_REPAIRABLE
PROSE_OMITTED                      BLOCKED MODEL_REPAIRABLE
TERMINOLOGY_INCORRECT              BLOCKED MODEL_REPAIRABLE
RESIDUAL_TRANSLATABLE_CYRILLIC     BLOCKED MODEL_REPAIRABLE
MARKDOWN_STRUCTURE_BROKEN          BLOCKED MODEL_REPAIRABLE
TECHNICAL_TOKEN_CHANGED            BLOCKED MODEL_REPAIRABLE
PLACEHOLDER_CHANGED                BLOCKED MODEL_REPAIRABLE
COMPANION_SYNTAX_BROKEN            BLOCKED DETERMINISTIC
IMAGE_BYTES_MISMATCH               BLOCKED DETERMINISTIC
ATOMIC_BUNDLE_INCOMPLETE           BLOCKED DETERMINISTIC
CONFLICTING_TARGET_WRITE           BLOCKED OPERATOR_REQUIRED
STYLE_SUGGESTION                   WARNING MODEL_REPAIRABLE
AMBIGUOUS_FENCE_STRING_RETAINED    WARNING DETERMINISTIC
~~~

STYLE_SUGGESTION remains warning and does not start repair by itself even though
it may accompany a related red repair. Operational gate codes render operational
reports and are not presented as translation-quality defects.

## 24.12 Persistence, keys, TTL, idempotency and recovery

### 24.12.1 Logical YDB schemas

Physical adapters MAY choose native YDB types but MUST preserve these keys and
constraints:

~~~text
ng_active_lineages
  PK (repository, source_pr)
  lineage_id, revision, state, translation_pr, branch, expires_at

ng_lineages
  PK (repository, lineage_id)
  source_pr, state, translation_pr, branch, continue_count,
  manifest_sha256, compact_snapshot_object_key,
  model_rotation_index, latest_translator_provider, latest_translator_model,
  latest_critic_provider, latest_critic_model,
  latest_verification_case_sha256,
  created_at, updated_at, expires_at, revision
  INDEX (repository, translation_pr)

ng_runs
  PK (repository, run_id)
  delivery_id, label_timeline_event_id, command, event_pr,
  source_pr nullable until §24.5.2 step 6, lineage_id,
  actions_run_id, actor, model_capable, phase, outcome, main_sha, checked_sha,
  started_at, finished_at, report_sha256, expires_at
  INDEX (repository, actions_run_id)
  INDEX (repository, source_pr, started_at)

At delivery claim, **event_pr** is persisted and **source_pr** is null. Step 6
fills **source_pr** and **lineage_id** in the same identity update before
active-run preflight.

ng_deliveries
  PK (repository, delivery_id)
  run_id, label_timeline_event_id, command, pr_number, created_at, expires_at

ng_locks
  PK (repository, source_pr)
  holder_run_id, holder_command, acquired_at, expires_at, revision

ng_decisions
  PK (repository, lineage_id, decision_id)
  kind, exact_scope_key, normalized_value, source_comment_id,
  source_comment_sha256, author_login, accepted_run_id, accepted_at, expires_at
  INDEX (repository, lineage_id, source_comment_id)

ng_model_calls
  PK (repository, run_id, call_id)
  source_pr, lineage_id, operation_id, role, pass_index, attempt_index,
  provider, model, prompt_version, request_sha256, state,
  provider_request_id nullable, reconciliation_evidence_sha256 nullable,
  outcome nullable, failure_code nullable, response_sha256 nullable,
  input_tokens nullable, output_tokens nullable, cost_rub nullable,
  reserved_at, reservation_moscow_day, finished_at nullable,
  transcript_object_key nullable, expires_at, revision
  INDEX (repository, reservation_moscow_day, state)
  INDEX (repository, finished_at)

ng_verification_cache
  PK (repository, case_sha256)
  verifier_version, checked_commit_sha, critic_response_object_key,
  result_object_key, created_at, expires_at

ng_comments
  PK (repository, owner_kind, owner_number, comment_kind)
  github_comment_id, run_id, body_sha256, detail_comment_ids, updated_at

ng_publications
  PK (repository, run_id)
  branch, expected_remote_sha, published_commit_sha, translation_pr,
  mutation_phase, tree_sha256, updated_at, expires_at

ng_rotation
  PK (repository)
  next_index, revision
~~~

Every retained row has exact expires_at. YDB TTL deletion MAY lag, but reads MUST
treat **expires_at <= now** as absent/expired. Lock TTL is two hours. Lineage
expiry follows §23 refresh rules. Runs, costs, snapshots, reports and transcripts
expire 14 days after their own run.

### 24.12.2 Artifact keys

Full bytes live in the configured artifact backend under:

~~~text
ng/v1/<repository>/<run_id>/manifest.json
ng/v1/<repository>/<run_id>/snapshots/<commit_sha>/<path_sha256>
ng/v1/<repository>/<run_id>/requests/<call_id>.json
ng/v1/<repository>/<run_id>/responses/<call_id>.json
ng/v1/<repository>/<run_id>/verification/<case_sha256>.json
ng/v1/<repository>/<run_id>/reports/<report_sha256>.md
ng/v1/<repository>/<lineage_id>/compact/<revision>.json
~~~

Keys contain no raw secret or URL query. Object metadata stores digest, size,
created_at and expires_at. Reads verify digest.

### 24.12.3 Idempotency

- Command delivery key: repository plus delivery_id. Duplicate delivery resumes
  or returns the existing terminal result; it never removes another label or
  starts another run.
- Paid call key: run_id plus deterministic
  **operation_id/role/pass_index/attempt_index**. The RESERVED insert and every
  later state change are compare-and-set. Once dispatch may have begun, neither
  duplicate delivery nor stale-lock takeover invokes that call ID again.
- Decision identity: lineage plus source comment ID plus normalized decision
  ordinal.
- Comment identity: owner PR plus lifecycle/QA kind. Bodies are updated, not
  duplicated.
- Publication identity: run ID plus tree digest. A repeated push is allowed only
  if the remote already equals the recorded commit.

The RESERVED row is committed before invoking the provider. A returned adapter
result and its response artifact are committed as RESULT_RECORDED before the
result is parsed or used. Missing usage stays null. Daily actual spend sums
non-null actual cost by the Europe/Moscow date containing finished_at. A call
crossing midnight is charged to its completion day. Admission is allowed when
spend is below budget and may overrun only through admitted calls.

Separately, any unresolved UNKNOWN_BILLED row whose reservation_moscow_day is the
current Europe/Moscow date blocks all new paid model calls, regardless of known
spend. It does not block a metadata-proven zero-call run and stops blocking at
the next Moscow-day boundary. Its unknown cost is not included as zero or as an
estimate. The row and reconciliation audit remain retained for 14 days.

Allowed model-call transitions are exactly:

~~~text
ABSENT -> RESERVED
RESERVED -> RESULT_RECORDED
RESERVED -> UNKNOWN_BILLED
UNKNOWN_BILLED -> RESULT_RECORDED
UNKNOWN_BILLED -> RECONCILED_NOT_BILLED
~~~

RESULT_RECORDED and RECONCILED_NOT_BILLED are terminal. UNKNOWN_BILLED remains
unresolved until one of its two authoritative transitions. No transition returns
to RESERVED, changes call identity/request digest/provider, overwrites a recorded
result or accepts a manually asserted cost.

### 24.12.4 Checkpoints and crash recovery

Every irreversible effect is preceded and followed by a persisted mutation phase:

~~~text
NONE
COMMAND_LABEL_REMOVE_INTENT -> COMMAND_LABEL_REMOVED
OLD_DRAFT_CLOSE_INTENT -> OLD_DRAFT_CLOSED
OLD_BRANCH_DELETE_INTENT -> OLD_BRANCH_DELETED
NEW_BRANCH_PUSH_INTENT -> NEW_BRANCH_PUSHED
DRAFT_CREATE_INTENT -> DRAFT_CREATED
FORCE_PUSH_INTENT -> FORCE_PUSHED
DRAFT_METADATA_UPDATE_INTENT -> DRAFT_METADATA_UPDATED
COMMENTS_UPDATE_INTENT -> COMMENTS_UPDATED
~~~

Recovery re-reads GitHub state and:

- treats the desired existing effect as complete;
- retries the same idempotent effect when absent;
- blocks on a conflicting remote effect;
- never unconditional-force pushes;
- deletes an orphan new branch only for a failed new-translate publication with
  no Draft and a matching recorded tree/commit;
- never deletes human commits or an unrecorded branch during compensation.

Command-label recovery is at-most-once with respect to a label reapplication. The
journal stores **label_timeline_event_id**. On recovery, fully paginate current
timeline events: if the label is absent or the latest active application has a
different event ID, the old effect is complete and the newer label is untouched;
if the same stored event ID is still the active application, retry its removal
and record COMMAND_LABEL_REMOVED. GitHub cannot apply another instance while the
label is present, so the event-ID compare followed by deletion cannot target a
newer application. Missing/incomplete timeline pagination is technical failure,
not permission to issue an unbound DELETE.

A recovered RESERVED model call means dispatch may have occurred. Before any
further paid call, recovery compare-and-sets it to UNKNOWN_BILLED and MUST NOT
dispatch it again. If the provider supports authoritative lookup tied to the exact
call/request identity, a reconciler may persist the exact recovered result as
RESULT_RECORDED or persist provider proof of non-acceptance/non-billing as
RECONCILED_NOT_BILLED. Reconciliation is monotonic and audited; UNKNOWN_BILLED
cannot be cleared from operator input, local absence of a response, timeout
classification or guessed usage. Providers without authoritative lookup leave
the state unresolved.

A crash before accepted continue completion does not refresh lineage expiry, but
the same delivery resumes the already consumed attempt. A different delivery
cannot run while the live lock exists. After stale-lock takeover it must
resume/close the prior mutation journal before starting new work.

If report persistence succeeds but GitHub comment update fails, the run remains
TECHNICAL_FAILURE and recovery retries the canonical comment without rerunning
models. If comment succeeds but terminal row write fails, duplicate delivery
discovers body/run marker and reconstructs the terminal row.

## 24.13 GitHub branch, PR, label and comment lifecycle

### 24.13.1 Lineage state derivation

Before a destructive command, persisted lineage is reconciled with GitHub:

| GitHub/persistence observation | Derived state | Continue | Translate |
|---|---|---|---|
| no Draft yet, valid unexpired lineage | WAITING_NO_DRAFT | allowed on source PR | clean restart |
| open Draft, branch exists, still Draft | DRAFT_OPEN | allowed on Draft | clean restart |
| open PR changed to Ready | READY_BLOCKED | blocked | clean restart |
| Draft closed without merge | DRAFT_CLOSED | blocked | clean restart |
| open Draft, branch absent | DAMAGED | blocked | clean restart |
| translation PR merged | MERGED | blocked forever | already complete |
| expires_at reached | EXPIRED | blocked | clean restart |
| multiple active translation PRs | inconsistent | blocked | blocked |

Manual commits on DRAFT_OPEN do not block continue. Continue report must warn
before the rebuild, and the rebuilt force push discards them. Human edits SHOULD
occur only after the final intended continue.

### 24.13.2 Exact allowed mutations

| Mutation | DOC_TRANSLATE | DOC_CONTINUE | DOC_VERIFY |
|---|---:|---:|---:|
| remove its one-shot command label | MUST | MUST | MUST |
| remove unrelated labels | MUST NOT | MUST NOT | MUST NOT |
| update canonical comment(s) | MUST | MUST | MUST |
| close old unfinished Draft on clean restart | MUST iff one exists | MUST NOT | MUST NOT |
| comment replacement on old Draft | MUST when closing | MUST NOT | MUST NOT |
| delete deterministic old translation branch | MUST iff it exists during clean restart | MUST NOT | MUST NOT |
| create deterministic branch | MUST iff safe diff is non-empty | MUST iff first safe diff and no Draft | MUST NOT |
| create Draft | MUST iff safe diff is non-empty | MUST iff first safe diff and no Draft | MUST NOT |
| commit/push content | MUST iff safe diff is non-empty | MUST iff rebuilt safe tree differs or first Draft | MUST NOT |
| force-with-lease update active Draft | MUST NOT for new lineage | MUST iff active Draft tree differs | MUST NOT |
| update NG Draft metadata marker | MUST after Draft creation | MUST after accepted continue | MUST NOT |
| change Draft to Ready | MUST NOT | MUST NOT | MUST NOT |
| merge PR | MUST NOT | MUST NOT | MUST NOT |
| edit a human branch | MUST NOT | MUST NOT | MUST NOT |
| create verify/fixup PR | MUST NOT | MUST NOT | MUST NOT |

The new Draft title/body identify source PR, source manifest hash, main snapshot
SHA, lineage ID and exact lineage **expires_at**. The body carries one
machine-readable invisible NG marker with those non-secret fields. Every accepted
continue updates main SHA and expires_at in that marker. After YDB/artifact TTL,
the marker is sufficient only to render the required expired-context source PR
and expiry report; it MUST NOT be accepted as manifest, decisions, snapshot or
continue context. A missing or human-corrupted marker with no live lineage is
LINEAGE_DAMAGED and never green. Branch is always **ydbdoc-review/pr-N**.
Automation creates Draft explicitly and never calls Ready-for-review or merge
APIs.

### 24.13.3 Canonical comments

Markers are invisible HTML comments:

~~~text
<!-- ydbdoc-ng:lifecycle source=<N> -->
<!-- ydbdoc-ng:qa owner=<N> -->
<!-- ydbdoc-ng:detail run=<run_id> part=<i>/<n> -->
~~~

Source PR has one lifecycle comment. Active Draft has one QA comment. Ordinary
verified PR has one QA comment. After translate, continue or DOC_VERIFY on a
recognized NG Draft, source lifecycle and Draft QA share that run's ID and links.
The source lifecycle comment contains no-Draft reasons, active Draft link,
lineage state and continue history. The Draft QA comment is bound to its exact
current commit. Detail comments are recreated for the current run when needed;
old detail comments are minimized or marked superseded, never mistaken for
current results. No red issue may be dropped due to GitHub comment size.

## 24.14 Russian user-facing report templates

Renderers substitute bracketed fields, escape untrusted Markdown and never expose
secrets. The summary always includes command, run ID, checked SHA and workflow
link when known.

### 24.14.1 Green success

~~~text
✅ Перевод и проверка завершены

Исходный PR: №[source_pr]
Проверенный коммит: [sha]
Направления: [directions]
Файлы: [root_count], зависимости: [dependency_count]
Проверки: структура, смысл, ссылки, меню, редиректы, глоссарий
Проходы автоисправления: [repair_passes_used] из 2
Результат: замечаний, мешающих слиянию, нет.

Черновик перевода: [draft_link]
~~~

For verify, first line is **✅ Проверка завершена**, and no Draft line is shown.

### 24.14.2 Yellow warning

~~~text
🟡 Проверка пройдена с предупреждениями

Проверенный коммит: [sha]
Слияние разрешено, но рекомендуем проверить [warning_count] замечаний.

[file]:[line]: [plain_russian_warning]
Фрагмент: «[fragment]»
Что сделать: [recommendation]
~~~

Retained RU internal link text MUST say that EN page/fragment is unavailable,
the working RU link was preserved, merge is allowed and a separate link-fix PR is
recommended.

### 24.14.3 Quality or operator blocker

~~~text
🔴 Перевод заблокирован

Проверенный коммит: [sha]
Безопасные наборы в черновике: [published_count]
Пропущенные наборы: [omitted_count]

[n]. [plain_russian_problem]
Источник: [source_path] [source_location]
Результат: [target_path] [target_location]
Было в источнике: «[source_fragment]»
Получилось: «[target_fragment]»
Что сделать: [action]
[ready_continue_command]

Проходы автоисправления: [repair_passes_used] из 2.
[active_lineage_next_action]
Слияние этого черновика запрещено до устранения всех красных пунктов.
~~~

**repair_passes_used** is the maximum completed repair pass index across bundles,
from 0 through 2. Technical details separately show total repair units and every
primary/fallback call, so a multi-bundle run never renders an impossible value
such as «5 из 2».

For an active lineage, **active_lineage_next_action** is mandatory and contains
the exact next action, a ready valid continue command when continue can resolve
it, and `Осталось попыток продолжения: [remaining] из 3.` For an ordinary human
PR it contains only a manual fix instruction and no continue counter/command.

If a reliable line is unavailable, renderer replaces it with exact heading,
Markdown element, YAML/JSON path, TOC hierarchy or fragment. It MUST NOT print an
approximate line number.

### 24.14.4 No-Draft lineage

~~~text
🔴 Перевод начат, но безопасный черновик пока нельзя создать

Причина: [reason]
Файлы: [paths]
Контекст сохранён до [expires_at].
Ответьте в этом исходном PR и затем снова поставьте метку doc_continue:

[ready_command]

Первый успешный doc_continue создаст Draft PR.
Осталось попыток продолжения: [remaining] из 3.
~~~

### 24.14.5 No translation required

~~~text
✅ Перевод не требуется

Проверены текущие версии файлов из main на коммите [sha].
[per_path_reasons]
Безопасных изменений для отдельного Draft PR нет.
~~~

For bilingual NO_TRANSLATION, append exactly two path-specific force choices per
waiting pair. For one-direction semantic no-op, append the one path-specific
force choice.

### 24.14.6 ACL denial

~~~text
🔴 Команда не выполнена

Перевод не проверялся. Пользователь @[login] запустил [command], но у него
недостаточно прав. Для doc_continue также проверяется автор команды
/ydbdoc continue: @[comment_author].
Изменения веток и вызовы моделей не выполнялись.
~~~

### 24.14.7 Budget denial

~~~text
🔴 Дневной бюджет исчерпан

Перевод не проверялся. Лимит на [moscow_date]: [budget] ₽.
Уже учтено фактических расходов: [spent] ₽.
Новый московский день начнётся [next_day_utc] UTC ([next_day_msk] МСК).
Изменения веток и вызовы моделей не выполнялись.
~~~

Unresolved billing is rendered separately and never called a spent-budget
result:

~~~text
🔴 Не удалось подтвердить расход на вызов модели

Перевод не проверялся. Вызов [call_id] модели [provider/model] мог быть принят
провайдером, но его результат и стоимость не удалось надёжно записать.
Повторно этот вызов не запускался. Стоимость неизвестна и не считается нулевой.
Новые платные вызовы заблокированы до [next_day_utc] UTC ([next_day_msk] МСК)
или до подтверждения провайдером. Проверки без вызовов моделей не блокируются.
[retry_instruction]
Изменения веток после этого сбоя не выполнялись.
~~~

For an active translation lineage, **retry_instruction** identifies the merged
source PR and explains that a later `doc_translate` is a clean destructive
restart. For an ordinary open PR it says to reapply `doc_verify` later and never
suggests the merged-only command on that PR. If authoritative reconciliation
resolves the call earlier, the canonical report is updated with the exact
provider result or proof that it was not billed; no manually supplied price or
usage is accepted.

### 24.14.8 Concurrency denial

~~~text
🔴 Работа уже выполняется

Для исходного PR №[source_pr] уже запущена команда [other_command].
Начало: [started_at]
Workflow: [workflow_link]

Перевод или проверка не выполнялись. После завершения снова поставьте метку
[label].
~~~

### 24.14.9 Model failure

Translation:

~~~text
🔴 Перевод не удалось получить

Настроенные модели не вернули корректный результат. Файл: [path].
Попробуйте позже снова поставить doc_translate на объединённый исходный PR.
Повторный doc_translate начнёт всё заново: закроет текущий Draft, удалит ветку
перевода и отбросит решения doc_continue.
~~~

Critic:

~~~text
🔴 Качество перевода не удалось проверить

Настроенные модели проверки не вернули корректный структурированный результат.
Мы не считаем этот перевод проверенным и не выдумываем дефекты.
[next_action]
~~~

For an active translation lineage, **next_action** is the same clean
DOC_TRANSLATE retry and destructive-restart warning required by §23.10. For an
ordinary open human PR, it says to reapply **doc_verify** later and states that
the bot made no repository-content changes; it MUST NOT suggest an invalid
merged-only translate on that open PR.

### 24.14.10 Expired lineage

~~~text
🔴 Контекст перевода истёк

Исходный PR: №[source_pr]
Контекст и решения были доступны до [expires_at].
Продолжить или автоматически проверить текущий Draft нельзя.

Поставьте doc_translate на объединённый исходный PR. Это чистый перезапуск:
текущий Draft будет закрыт, ветка удалена, решения продолжения отброшены.
~~~

### 24.14.11 Additional lifecycle blockers

Ready:

~~~text
🔴 Продолжение остановлено: PR переведён из Draft в Ready.
Сначала верните [pr_link] в Draft, затем снова поставьте doc_continue.
~~~

Duplicate Draft:

~~~text
🔴 Найдено несколько активных PR перевода для исходного PR №[source_pr]:
[links]. Закройте дубликаты вручную и повторите команду.
~~~

Wrong continue location:

~~~text
🔴 Команда оставлена не в том PR.
После создания Draft все команды продолжения нужно писать в [correct_pr_link].
Модель не вызывалась, попытка не израсходована.
~~~

Depth overflow:

~~~text
🔴 Превышена глубина зависимостей для [root].
Цепочка: [a] → [b] → [c]
Разрешено: [effective_depth], требуется: [required_depth].
/ydbdoc continue разреши глубину [required_depth] для [root]
~~~

## 24.15 Configuration and secret handling

### 24.15.1 Repository variables

| Name | Required value/format | Rule |
|---|---|---|
| YDBDOC_ALLOWED_ACTORS | comma-separated logins | Required, fail closed; confirmed initial value **sintjuri,SixOnMyface,nataliaboldyreva,ayakivosklznak** |
| YDBDOC_DAILY_BUDGET_RUB | non-negative decimal RUB | Required; Moscow-day admission |
| YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE | integer **100** | Required; changing from 100 requires product-contract change |
| YDBDOC_NG_CLASSIFIER_MODELS | ordered comma-separated provider/model IDs | Required |
| YDBDOC_NG_TRANSLATOR_ROTATION | ordered provider/model IDs | Required for translation |
| YDBDOC_NG_CRITIC_ROTATION | ordered provider/model IDs | Required for verification |
| YDBDOC_NG_TRANSLATOR_FALLBACKS | ordered provider/model IDs | Optional, maximum one attempted |
| YDBDOC_NG_CRITIC_FALLBACKS | ordered provider/model IDs | Optional, maximum one attempted |
| YDBDOC_NG_REPAIR_FALLBACKS | ordered provider/model IDs | Optional, maximum one attempted |
| YDBDOC_NG_VERIFIER_VERSION | immutable deployed version string | Required |
| YDBDOC_NG_MODEL_PRICING_VERSION | immutable tariff-table version | Required; included in case/audit metadata |
| YDBDOC_TRANSCRIPT_BACKEND | ydb or s3 | Required |

Fixed, non-configurable contract constants are docs root, locale roots, TOC names,
glossary paths, redirect path, depth 3, dependency count 100, continue maximum 3,
lock TTL two hours, retention 14 days, command labels, branch pattern and
single-language initial manifest.

Model-list parsing trims whitespace, rejects empty elements and deduplicates only
exact duplicate identifiers while preserving first order. Provider/model is one
opaque identifier for independence comparison.

ACL parsing splits on comma, trims ASCII whitespace and compares GitHub logins
case-insensitively after ASCII lowercase. Empty elements are discarded, but an
empty resulting set is configuration failure.

Budget and cost use base-10 Decimal, never binary float. Persistence keeps the
provider/tariff precision; reports round half-up to two decimal RUB only for
display. Admission compares unrounded values.

### 24.15.2 Secrets

Adapters MAY consume:

- **GITHUB_TOKEN** for event-scoped label/comment/read access;
- **YDBDOC_PUSH_PAT** for branch and Draft mutations;
- **YDBDOC_YC_API_KEY** and **YDBDOC_YC_FOLDER_ID** for Yandex model access;
- **ELIZA_OAUTH_TOKEN** only for explicitly local Eliza fallback execution;
- **YDB_SA_KEY** for YDB;
- **YDBDOC_S3_ACCESS_KEY_ID** and **YDBDOC_S3_SECRET_ACCESS_KEY** for S3.

Production label workflows default to Yandex Cloud. Eliza MUST NOT silently become
the production default. A missing required secret blocks before paid/model or
branch work.

Secret values MUST NOT enter domain DTOs, hashes, logs, exceptions, model prompts,
transcripts, artifact metadata, comments or persisted config snapshots.
Observability records only secret name and PRESENT/MISSING. HTTP logging redacts
Authorization, cookies, query credentials and provider request IDs that embed
tokens.

## 24.16 Observability and audit

Every run log/event MUST include:

~~~text
timestamp, level, event_name, repository, command, run_id, delivery_id,
label_timeline_event_id,
actions_run_id, source_pr, translation_pr, lineage_id, lineage_revision,
actor, continue_comment_author, model_capable, phase, outcome,
source_manifest_sha256, main_sha, checked_sha, case_sha256,
bundle_id, root_path, direction, issue_rule_id,
model_role, pass_index, attempt_index, provider, model,
call_id, model_call_state, call_outcome, provider_request_id,
reservation_moscow_day, reconciliation_evidence_sha256,
input_tokens, output_tokens, actual_cost_rub,
lock_holder_run_id, duration_ms, retryable_boolean, failure_code
~~~

Fields not applicable are null, not omitted. Paths and exact fragments may appear
only in retained structured evidence, not high-volume metric labels.

Required counters/histograms:

- commands received/accepted/rejected by command and reason;
- active-run and lock conflicts;
- manifest pages/entries and contradiction failures;
- roots, dependency counts/depth overflows/cycles;
- classifier verdicts and exhaustion;
- model attempts, fallback, malformed and failure by role/model, including
  RESERVED recovery, UNKNOWN_BILLED age and authoritative reconciliation result;
- input/output tokens and actual RUB;
- verifier verdicts/issues by stable rule ID;
- bundles safe/omitted/published and publication mutations;
- cache hit/miss;
- lineage state, accepted continues and expirations;
- report/comment update failures and recovery actions.

Audit records MUST permit reconstruction of who issued each label and instruction,
what exact bytes and config were used, every model attempt/state/cost, whether
each call ID could have been dispatched, every authoritative reconciliation
record, every decision, every GitHub mutation and final result. They are retained
14 days.

## 24.17 Migration, cutover and rollback

§25.9 is the only executable migration, cutover and rollback sequence. Cutover
and NG `doc_translate` remain prohibited until its preconditions receive explicit
independent PASS and human approval.

The retained behavioral invariants are:

- exactly one command-label writer at every instant;
- existing legacy Drafts are not interpreted as NG lineage and cannot continue;
- eventual verification treats a legacy Draft as an ordinary open PR;
- legacy restart discovery is fail-closed on ownership, base, branch and
  duplicates;
- rollback revokes NG write capability before enabling one legacy writer;
- NG Drafts, lineage, calls and effects are preserved for audit and never
  auto-mutated by legacy.

## 24.18 Numbered acceptance criteria

An implementation is not cutover-ready unless every applicable criterion has an
independently authored executable test in the separate acceptance repository and
all tests pass against the release OCI image. Product-owned unit tests are useful
engineering evidence but cannot satisfy an NG-AC criterion by themselves.

### Authority and architecture

- **NG-AC-001.** The production artifact is built only from the separate
  `ydbdoc-review-ng` repository and exposes the `ydbdoc_ng` distribution through
  one versioned executable contract.
- **NG-AC-002.** Source, lockfile, wheel and OCI scans reject every
  `ydbdoc_review` dependency and every legacy/failed-NG import, copy, plugin,
  subprocess, sidecar or other runtime closure.
- **NG-AC-003.** There is no legacy symbol allowlist. Legacy behavior is input
  evidence only and never supplies an NG acceptance expectation.
- **NG-AC-004.** Domain and pure verifier have no filesystem, environment, git,
GitHub, clock, network or model access.
- **NG-AC-005.** Provider adapters make exactly one request per invocation and hide
no retry/fallback.
- **NG-AC-006.** Exactly one production router can mutate GitHub for command labels.
- **NG-AC-007.** Legacy results are never used as NG policy expectations.
- **NG-AC-008.** Fixed paths, constants and enums serialize exactly as specified.

### Events, gates, labels and concurrency

- **NG-AC-009.** Only exact labeled events start production commands.
- **NG-AC-010.** A continue comment alone makes zero model calls and zero branch
mutations.
- **NG-AC-011.** Each command removes only its own one-shot label on acceptance and
all rejection paths.
- **NG-AC-012.** Label removal failure stops before all model/content work.
- **NG-AC-013.** Actor is the top-level labeled-event **sender.login**, never PR
author, **pull_request.user.login** or bot.
- **NG-AC-014.** Missing/empty allowlist fails closed.
- **NG-AC-015.** Continue requires both allowed label sender and allowed selected
comment author.
- **NG-AC-016.** ACL denial names login and command in Russian and states no
verification occurred.
- **NG-AC-017.** Translate rejects open and closed-unmerged source PRs before
manifest planning, budget use, model and branch work.
- **NG-AC-018.** Verify rejects every non-open PR.
- **NG-AC-019.** A metadata-proven model-capable run is denied before model and
destructive branch work when current Moscow-day actual spend is exhausted or
that day has unresolved UNKNOWN_BILLED; the Russian report distinguishes the two,
shows the applicable facts/next Moscow day and fails Action. A proven
zero-paid-call run skips both denials.
- **NG-AC-020.** Daily cost uses only actual idempotent call records and Moscow
calendar boundaries. No RUB amount is reserved or estimated; the durable
pre-request RESERVED marker is call safety, not spend. Deterministic-only,
single-language-only and unsupported-only scope does not consume budget
admission.
- **NG-AC-021.** Every deterministic call ID is durably RESERVED before dispatch,
is sent to its provider at most once, and records a returned result/cost before
downstream use. Recovery of an ambiguous RESERVED call yields UNKNOWN_BILLED,
stops later paid calls and never resends it; only exact authoritative provider
evidence may record its result or prove it not billed.
- **NG-AC-022.** Missing provider usage/cost is never estimated or stored as zero;
unresolved billing remains null, is reported explicitly and blocks paid work only
for its reservation Moscow day.
- **NG-AC-023.** Active-run preflight checks all pages, excludes the current
  Actions run ID, joins other run IDs to persisted event/source/lineage identity,
  applies deterministic earlier-run precedence, and shows the conflicting
  command/event PR/source/lineage/start/link without relying on source or lineage
  text in **run-name**; it makes zero model or content-branch changes.
- **NG-AC-024.** The per-source lock is compare-and-set, blocks all three commands
for one lineage and does not block another source PR.
- **NG-AC-025.** A lock cannot be stolen before two hours and can be recovered
after expiry with an audit record.
- **NG-AC-026.** A concurrent rejection removes its label, fails Action and says to
reapply it later.
- **NG-AC-027.** Delivery is claimed before label mutation; a duplicate
resumes/returns one run, label recovery compares the exact fully paginated
timeline event ID and cannot remove a newer application, and no paid call or
GitHub mutation is duplicated.

### Manifest, snapshots and pair operations

- **NG-AC-028.** Manifest requires merged=true and all three official SHAs.
- **NG-AC-029.** PR files pagination is complete, ordered and persisted with status
and previous_filename.
- **NG-AC-030.** Missing rename previous_filename, unknown status, incomplete page
or twice-contradictory PR identity blocks before models.
- **NG-AC-031.** Local checkout/diff cannot add, remove, reorder or reclassify
manifest operations.
- **NG-AC-032.** Current main SHA is captured once and every source byte is read by
that exact SHA.
- **NG-AC-033.** PR head/merge bytes never substitute for current-main translation
content.
- **NG-AC-034.** Locale pairing replaces only the first locale component under
**ydb/docs** and preserves the rest byte-for-byte.
- **NG-AC-035.** RENAME is exactly DELETE old plus ADD new with no MOVE.
- **NG-AC-036.** Recorded PR 45949 is classified as independent delete and add even
when TOC evidence supplies a successor; the deletion bundle depends atomically
on the add bundle without becoming MOVE.
- **NG-AC-037.** One-locale add/update selects that locale and fully overwrites the
other current target.
- **NG-AC-038.** Both-locale add/update invokes complete-file bilingual
classification.
- **NG-AC-039.** Both-locale delete produces no translation change.
- **NG-AC-040.** Delete/write mixed pair is operator-required and safe independent
bundles may still publish red.
- **NG-AC-041.** Missing current source for original add/update is SUPERSEDED and
historical content is not restored.
- **NG-AC-042.** Recreated current source for original delete is SUPERSEDED and
target is not deleted.
- **NG-AC-043.** If either side of original bilingual write is missing, whole pair
is SUPERSEDED and survivor is not reclassified.
- **NG-AC-044.** Empty final safe diff creates no Draft and reports per-path
reason; only a continue-resolvable question creates an active WAITING_NO_DRAFT
lineage, while a terminal no-op retains audit artifacts without a continuable
lineage.

### Lineage and continue

- **NG-AC-045.** First translate that needs retained continue/Draft context creates
a new active lineage with branch **ydbdoc-review/pr-N**, zero continues and no
decisions; a terminal no-op has only its audit run under NG-AC-044.
- **NG-AC-046.** Repeated translate on unfinished lineage, after gates, comments and
closes old Draft, deletes its branch, discards decisions/count and creates a new
lineage while retaining old audit artifacts.
- **NG-AC-047.** Merged translation lineage is terminal forever; translate and
continue make no content/branch changes and translate reports ALREADY_COMPLETE
before budget/model gates.
- **NG-AC-048.** Multiple active translation PRs block every destructive command.
- **NG-AC-049.** Continue is allowed for open Draft with branch and for valid
no-Draft lineage at source PR.
- **NG-AC-050.** Continue blocks Ready, closed, damaged, merged, replaced and expired
lineages with the specified Russian next action.
- **NG-AC-051.** Before Draft creation continue is accepted only on source PR;
after creation only on Draft. Wrong location consumes no attempt or comment.
- **NG-AC-052.** Latest applicable unconsumed continue comment is selected
deterministically; author/body digest is re-read before acceptance and an edit
restarts selection plus ACL before any attempt is consumed.
- **NG-AC-053.** One comment can create multiple exact scoped decisions.
- **NG-AC-054.** Ambiguous/path-unknown/conflicting decision is rejected before
snapshot/model/branch work and does not consume one of three attempts.
- **NG-AC-055.** Exactly three accepted continues are allowed; fourth is rejected.
- **NG-AC-056.** Accepted continue atomically consumes comment, appends decisions
and increments count even when final run is red.
- **NG-AC-057.** Continue captures latest main, reuses original manifest/scope
directions, replays all decisions and rebuilds from zero.
- **NG-AC-058.** Continue ignores and, on push, discards every manual branch byte
and commit.
- **NG-AC-059.** Continue force update uses force-with-lease; lease failure never
becomes unconditional force.
- **NG-AC-060.** First continue with safe diff creates the first Draft for a
no-Draft lineage.
- **NG-AC-061.** Accepted continue refreshes compact lineage expiry to completion
plus 14 days; denials and verify do not.
- **NG-AC-062.** One-locale force overrides only semantic no-op for exact pair.
- **NG-AC-063.** Bilingual force persists selected authority plus force for exact
pair through later rebuilds.
- **NG-AC-064.** Pathless force is accepted only under the exact single-waiting-pair
rules; otherwise ready path-specific commands are repeated.

### Scope, single-language and dependencies

- **NG-AC-065.** Central single-language manifest initially contains only
**public-materials/\*** and matches complete locale-relative subtree.
- **NG-AC-066.** Single-language bytes, links, includes, assets, Cyrillic and TOC
entries are not inspected; mirror/parity is not required.
- **NG-AC-067.** Only a skip count is reported for single-language paths.
- **NG-AC-068.** Every unsupported manifest path is named and unchanged; a direct
unsupported locale file is yellow, an outside-scope path is informational, and
unsupported-only scope creates no Draft and passes.
- **NG-AC-069.** Unsupported mandatory parsed dependency is red with exact chain and
blocks only requiring bundles.
- **NG-AC-070.** Dependency expansion uses parsed locale-local include, image and
allowlisted companion-link nodes only.
- **NG-AC-071.** Plain text, comment, code fence, HTML, unknown syntax, neighbor
directory and ordinary Markdown-article link do not expand scope.
- **NG-AC-072.** Markdown includes recurse; companions/images are leaves; TOCs use
separate scope.
- **NG-AC-073.** Every edge increments depth; root is pre-marked visited at depth
zero and never counted/re-enqueued; cycles stop without error and each canonical
path is processed once.
- **NG-AC-074.** Default depth is 3. Overflow blocks root, shows complete chain and
ready exact-root command.
- **NG-AC-075.** Accepted depth decision changes only exact root closure numeric
limit and persists in lineage.
- **NG-AC-076.** Dependency count excludes root/TOC/redirect, includes unique
includes/images/companions and is calculated independently per root.
- **NG-AC-077.** Count exceeding 100 blocks and cannot be overridden.
- **NG-AC-078.** Direct companion is standalone root and shared dependency counts
once in each root closure; a same-direction shared output is generated once and
is not published when every requiring root is omitted.

### Images, companions and documents

- **NG-AC-079.** Allowed images copy byte-for-byte in either direction and report
paths, size, hash; no OCR/model/classifier is used.
- **NG-AC-080.** Standalone both-locale images with equal hashes are no-op; unequal
hashes require exact authority choice.
- **NG-AC-081.** Directional document owns reached image over conflicting opposite
direct edit and report names overwrite.
- **NG-AC-082.** Opposite-direction roots with differing shared image block all
requiring bundles.
- **NG-AC-083.** Markdown/YFM and documentation includes translate fully in both
directions, including allowed front matter.
- **NG-AC-084.** RU_TO_EN YAML, JSON, TXT and C/C++ translate only specified
locations and preserve all protected content.
- **NG-AC-085.** EN_TO_RU companions copy complete bytes as-is except Markdown/YFM.
- **NG-AC-086.** Syntax damage is red; residual Cyrillic is red only in required
RU_TO_EN translatable locations.
- **NG-AC-087.** Fence boundaries/language/code/tokens remain exact; RU comments
and clear user-facing strings translate, ambiguous strings stay yellow; EN_TO_RU
fence body stays English.
- **NG-AC-088.** After two failed safe repair cycles, unsafe companion/document
bundle is omitted and independent safe bundles remain publishable.
- **NG-AC-089.** Explicit current-applicable asset/companion delete removes target
mirror and scans references only in scoped closures.

### Classifiers and model calls

- **NG-AC-090.** One-direction semantic classifier fail-open on all technical
failures; bilingual exhaustion is red without guessed authority.
- **NG-AC-091.** Valid AMBIGUOUS stops classifier chain immediately.
- **NG-AC-092.** Classifier calls each configured identifier at most once in order.
- **NG-AC-093.** If all pairs are NO_TRANSLATION, no Draft is made and the short
source report is posted.
- **NG-AC-094.** NO_TRANSLATION lineage persists 14 days and provides correct
path-specific force commands.
- **NG-AC-095.** A/B selection uses deterministic ring, different identifiers and
advances on later translate/continue; exact selected identifiers are persisted so
configuration reorder cannot reinterpret a lineage index.
- **NG-AC-096.** No distinct A/B pair is red independent-verification unavailable.
- **NG-AC-097.** Initial translator performs one primary and at most one eligible
fallback attempt.
- **NG-AC-098.** Each critic pass performs primary, same-model format repair only
for malformed output, then at most one fallback.
- **NG-AC-099.** Invalid critic exhaustion reports unavailable quality and invents
no defect.
- **NG-AC-100.** Role sequence is A translate, B verify/repair, A verify/repair,
B final verify with at most two repairs and three verifies.
- **NG-AC-101.** Repair technical failure consumes that repair attempt, leaves bytes
unchanged and proceeds to next verification.
- **NG-AC-102.** Only MODEL_REPAIRABLE issues enter repair; operator/operational
issues never do.
- **NG-AC-103.** Model calls per unit satisfy exact normal/minimum/maximum formulas,
each differing bilingual glossary entry is one classifier unit, call indices and
IDs follow the specified convention, a lifted shared dependency has one call unit
rather than one per root, and there are no hidden segmentation calls.
- **NG-AC-104.** Every call records exact role, pass, actual model, durable call
state, tokens, returned cost and failure before final report; an ambiguous call
records UNKNOWN_BILLED with null usage/cost and its exact call identity.
- **NG-AC-105.** Plain Russian translator/critic failure recommends the valid
later retry and warns of destructive restart where applicable. Unknown billing
instead explains at-most-once safety, the paid-work-until-next-Moscow-day block
and that unknown cost was not treated as zero.

### Links

- **NG-AC-106.** Internal ydb.tech docs URLs are recognized before external markers.
- **NG-AC-107.** External RU/EN markers use only parsed host/path/query rules,
case-insensitively, without download/LLM.
- **NG-AC-108.** RU_TO_EN internal path rewrites only to existing/same-candidate EN
target; fragment requires exact EN fragment.
- **NG-AC-109.** Missing EN target/fragment retains complete working RU URL and is
yellow mergeable with exact location.
- **NG-AC-110.** EN_TO_RU uses existing RU mirror, otherwise retains EN link.
- **NG-AC-111.** Only official RU Wikipedia interlanguage mapping to existing
canonical EN page is automatic; redirects are followed.
- **NG-AC-112.** Wikipedia exact fragment must exist; no translated/semantic
fragment match occurs.
- **NG-AC-113.** Wikipedia timeout/429/5xx/network/missing mapping and other RU
external URLs create blocking stable placeholder.
- **NG-AC-114.** Marker-conflict URL creates the same unresolved placeholder.
- **NG-AC-115.** One exact original URL maps to one deterministic NNN placeholder
through lineage and is never renumbered by continue.
- **NG-AC-116.** Operator mapping accepts syntactically valid absolute HTTPS only,
applies all exact occurrences and performs no availability probe.

### TOC and redirects

- **NG-AC-117.** Only three recognized TOC filenames enter navigation policy.
- **NG-AC-118.** Scoped add inserts missing target href, translates only label and
uses exact unambiguous parent/sibling placement.
- **NG-AC-119.** Existing target TOC is never fully mirrored or overwritten.
- **NG-AC-120.** Missing target TOC is fully created from source hierarchy/order,
translated labels and mirrored hrefs, with ordered arbitrary service data copied.
- **NG-AC-121.** Missing out-of-scope target page in newly created TOC retains
source link yellow; single-language link has no warning.
- **NG-AC-122.** Direct TOC root uses empty base/current only for applicable
ADD/DELETE, computes base-to-current source operations and applies only
add/remove/move/label/href/include changes; a one-locale structural delta cannot
be suppressed by semantic-no-op classification. Existing-target CST tests prove
all bytes outside edited spans remain identical, including comments, scalar style
and nested service fields.
- **NG-AC-123.** Ambiguous target node, parent, duplicate or sibling order omits
affected bundle and asks exact operator question.
- **NG-AC-124.** Complete source TOC delete preserves target-only nodes/service data
and deletes target file only if scoped result is empty.
- **NG-AC-125.** Every removed href, including subtree/file delete, has a separately
validated redirect in same atomic bundle.
- **NG-AC-126.** Redirect evidence is limited to exact source-registry append, same
TOC position replacement or operator mapping.
- **NG-AC-127.** Redirect destination exists/current-or-candidate and remains in
target locale; cross-locale redirect is forbidden.
- **NG-AC-128.** Identical existing redirect is no-op; conflict is never
overwritten; chains are not collapsed; lossless append leaves every existing
registry byte/order/comment/style unchanged.
- **NG-AC-129.** Unresolved redirect omits deletion, TOC removal and redirect
together but permits independent safe bundles.
- **NG-AC-130.** Direct registry append is evidence/no standalone candidate;
it is derived only from exact base-to-merge registry bytes and must still exist
identically in current main. Later-main-only entries are not attributed to the
source PR; direct modification or deletion is a red exact-entry report.
- **NG-AC-131.** Genuine deletion without successor remains blocked until exact
valid redirect target; no delete-without-redirect exception.

### Glossary

- **NG-AC-132.** Fixed RU/EN glossary pair is the only terminology authority and
exact snapshots are recorded.
- **NG-AC-133.** Entry starts at level-three-or-deeper heading and ends at next same
or higher heading.
- **NG-AC-134.** Duplicate anchor is red at every line; same-anchor rename is update;
anchor change is delete plus add.
- **NG-AC-135.** Anchorless pairing uses ordinal within stable-anchor interval only;
ambiguous interval requires mapping/authority, never title/LLM similarity.
- **NG-AC-136.** One-locale direct glossary change has full entry scope;
its skeleton, authoritative order and target-only deletions are synchronized.
Both-locale change harmonizes changed entries independently, and disjoint safe
GlossaryEdits compose deterministically into one Write per glossary file.
- **NG-AC-137.** Entry classifier selects clear authority or AMBIGUOUS; safe entries
may publish in red Draft.
- **NG-AC-138.** Usage detection is only explicit anchor link or exact case-folded,
whitespace-collapsed visible title.
- **NG-AC-139.** Directional article depends atomically on its used-entry bundle,
which harmonizes from the same locale; unrelated drift is not changed/blocking.
- **NG-AC-140.** Opposite-direction term conflicts and full-glossary/opposite-article
conflict omit entry and all dependent bundles until operator authority.
- **NG-AC-141.** Whole-glossary structural parse failures are red regardless of
entry scope.

### Verification, severity and publication

- **NG-AC-142.** Translate, continue and verify call exactly one shared verifier
service and identical case has identical semantics.
- **NG-AC-143.** VerificationCase contains exact snapshots, scope, directions,
overlay, deletes, manifest, decisions, glossary and versioned config; no ambient
read occurs.
- **NG-AC-144.** Case hash changes for any byte, scope, direction, decision, rule,
prompt, model or behavior config change.
- **NG-AC-145.** Exact cache hit from actual reconstructed PR bytes reruns
deterministic checks, makes zero critic calls and reuses complete result only;
publication/checked commit rebinding alone does not miss, but any byte change or
red partial-overlay difference does.
- **NG-AC-146.** Any case mismatch reruns complete critic scope, never partial
per-file cache.
- **NG-AC-147.** Verify uses a stable fully paginated API path set and exact base/head
bytes of the open PR; hypothetical or retained candidate bytes cannot turn the
unchanged actual PR green.
- **NG-AC-148.** Ordinary both-locale PR is checked without authority; one-locale
PR gets exact MISSING_LOCALE_TRANSLATION unless exempt.
- **NG-AC-149.** Verify scope is PR plus local dependencies, never repository-wide.
- **NG-AC-150.** BLOCKED fails, PASS_WITH_WARNINGS succeeds, PASS succeeds; yellow
alone triggers no repair.
- **NG-AC-151.** Every published bundle is complete; unsafe/blocked bundle and all
dependents are omitted, including article dependencies on glossary-entry bundles;
an orphaned safe shared dependency is not published.
- **NG-AC-152.** Safe independent bundles publish together in a red Draft and are
listed separately from omitted bundles.
- **NG-AC-153.** Conflicting writes for one path are red and neither version is
published; TOC/redirect intents compose only under exact preconditions, and only
disjoint verified GlossaryEdits may compose into one path. Any incompatible
overlap or composition failure blocks every affected bundle.
- **NG-AC-154.** Final overlay receives a whole-overlay deterministic validation
before one atomic commit.

### GitHub lifecycle and reports

- **NG-AC-155.** Translation output is always Draft and automation never readies or
merges it.
- **NG-AC-156.** Verify makes no branch, PR, commit, push, delete or repair mutation,
including technical failure paths.
- **NG-AC-157.** Source lifecycle and Draft QA comments are canonical, share the
latest translate/continue/NG-Draft-verify run ID and cross-link; ordinary or
legacy verify has one canonical QA comment.
- **NG-AC-158.** Later runs update comments instead of duplicating them.
- **NG-AC-159.** Oversized blockers split into numbered details without dropping or
hiding any red issue.
- **NG-AC-160.** Green report is short with SHA/directions/counts/categories and
0..2 repair passes; technical details retain total repair units and every
model/tokens/cost/version/file/mapping record.
- **NG-AC-161.** Every red issue contains exact files, reliable location or
structural locator, short fragments, source/result difference and concrete action.
- **NG-AC-162.** Residual Cyrillic report includes exact file, line and fragment.
- **NG-AC-163.** No report uses approximate line as exact or unexplained internal
jargon.
- **NG-AC-164.** Active lineage red report includes attempts, remaining continues
and ready command; human PR report gives manual fix only.
- **NG-AC-165.** Every accepted/rejected event gets a clear comment so label removal
cannot imply success; a denial on a new commit replaces any prior green current
result with the explicit not-verified operational report.

### Retention, recovery and operations

- **NG-AC-166.** Runs, actual costs, RESERVED/UNKNOWN_BILLED call state,
reconciliation evidence, decisions, snapshots, reports and full transcripts have
independent 14-day expiry and no secret values.
- **NG-AC-167.** Initial lineage expires 14 days after translate completes;
accepted continue refreshes compact lineage only; verify/denials do not.
- **NG-AC-168.** Expired open Draft cannot continue or become green; verify reports
expiry/source from retained context or the non-secret Draft marker and only clean
translate recovery; the marker alone can never restore verification context.
- **NG-AC-169.** The run_id plus call_id CAS survives crashes, permits at most one
provider dispatch, and allows only the normative monotonic model-call state
transitions.
- **NG-AC-170.** Mutation journal recovers desired effects, blocks conflicts and
never deletes/unconditional-force-pushes unrecorded human state.
- **NG-AC-171.** Report failure recovery does not rerun RESULT_RECORDED,
RECONCILED_NOT_BILLED or UNKNOWN_BILLED calls, and a recovered RESERVED call is
made UNKNOWN_BILLED rather than dispatched again.
- **NG-AC-172.** NG never starts/waits/interprets external docs build.
- **NG-AC-173.** Legacy Draft without NG lineage cannot continue and requires clean
translate restart; only one exact bot-owned deterministic-branch/base match may be
closed/deleted. Ambiguous or human-owned state blocks, while verify remains
read-only and treats the PR as ordinary.
- **NG-AC-174.** Cutover enables one writer atomically; rollback disables NG before
enabling legacy and preserves NG Drafts for human handling.
- **NG-AC-175.** All required audit fields reconstruct event, bytes, decisions,
every model-call state/possible dispatch/actual or unknown cost/reconciliation,
GitHub mutations and final result.

## 24.19 Independent test matrix

The matrix is owned by `ydbdoc-review-ng-acceptance`. These suites do not live in
the production tree and do not import product modules. “Unit”, “property” and
“static” describe the scope of the observable contract or artifact inspection,
not permission to call production internals. §25.4 governs red-baseline,
predicate-authorship and formal-review evidence.

| Suite | Level | Fixtures/faults | Acceptance coverage |
|---|---|---|---|
| import-boundaries | source/wheel/OCI static | every legacy/failed-NG dependency, dynamic plugin, subprocess/sidecar closure, cycle, second router | 001–007 |
| domain-contract | protocol/property | enum unknowns, canonical JSON, path traversal, digest mutation, collection order | 008, 028–044, 143–146 |
| event-gates | contract | top-level sender versus PR author, paginated label timeline IDs, duplicate delivery after label reapply, exhausted-budget and current-day UNKNOWN_BILLED for model-capable versus zero-call scope, empty ACL, two actors, label API failure | 009–027 |
| actions-lock | adapter/fault | paginated runs, current exclusion, same event PR before identity persistence, different event PRs with one source, matching lineage, unrelated sources, two runs observing each other, missing persisted identity, exact conflict report URL, near-simultaneous CAS, stale lock | 023–027 |
| manifest-api | adapter/recorded | merge/squash/rebase, 101+ files, Link pagination, changing identity, bad rename | 028–036 |
| pr-45949-move | recorded regression | exact source manifest and TOC evidence from PR 45949 | 035–036, 122, 125–131 |
| snapshot | adapter | main advances mid-run, missing blob, wrong SHA/size, ambient checkout differs | 032–033, 041–044 |
| pair-table | table/property | every RU/EN op combination, later create/delete, mixed pair | 037–044 |
| lineage | state-machine/model-based | all state rows, edited-comment author/body race, 0–4 continues, wrong PR, duplicate Draft, manual commits | 045–064 |
| single-language | unit/negative | content with broken links/includes/assets/Cyrillic under pattern | 065–067 |
| dependency-graph | property | root-return cycle, repeats, depth 3/4/5, 100/101 files, shared roots, misleading raw text | 068–078 |
| images | byte/property | all extensions, equal/different dual edits, shared opposing roots | 079–082 |
| companions | golden/parser | YAML keys/comments, JSON keys/values, TXT, every C/C++ suffix, malformed syntax | 083–089 |
| model-state-machine | external provider spy | every valid/error/malformed/fallback branch, per-entry glossary units, stable call IDs, and one wire invocation when the process dies after provider return but before result persistence | 021–022, 090–105, 169, 171 |
| links | golden/fault | relative/root/ydb.tech, marker combinations, Wikipedia redirect/fragment/429/5xx | 106–116 |
| toc-operations | golden/property | lossless comments/styles/nested service fields, applicable add/delete empty-tree boundary, no-text structural delta, missing target, insert anchors, duplicate href, move, include path, whole delete | 117–125 |
| redirects | table/property | lossless append bytes/comments/styles, base/merge/current provenance, later-main false evidence, three evidence kinds, no successor, conflict, chain, cross-locale, append-only violation | 125–131 |
| glossary | golden/property | anchors, rename/change, ambiguous intervals, disjoint/overlapping entry composition, direct/full/used/opposite directions | 132–141, 151, 153 |
| verifier-core | black-box differential | same case from all callers, issue ordering, severity, evidence locations | 142–154 |
| verification-cache | integration | internal-to-published commit rebind hit, manual-byte and red-partial-overlay miss, every hash input mutation, expired record | 144–146 |
| atomic-publication | fault/property | unsafe member, shared dependency, multi-bundle TOC/redirect/glossary composition, conflicting write, zero/some safe bundles | 151–154 |
| github-lifecycle | external GitHub simulator/fixture repo | clean restart checkpoints, Draft creation, force lease fail, forbidden verify methods | 155–165, 170–171 |
| reports-ru | snapshots | all §24.14 templates, separate spent-budget and unknown-billing reports, multi-bundle repair-pass count, escaping, oversized detail split, line unknown | 016, 019, 022–023, 094, 105, 159–165, 168 |
| persistence | real YDB/emulator integration | call CAS and state-transition table; crashes before dispatch, after dispatch/before result write, after result write and after transcript write in fresh processes; current-day unknown blocks paid but not zero-call work; Moscow midnight; authoritative found/not-billed/inconclusive reconciliation; delayed TTL; expired Draft marker; mutation recovery | 020–022, 061, 166–171, 175 |
| secrets-audit | static/runtime | sentinel secrets in env/errors/provider responses | 166, 175 |
| migration-router | deployment contract | legacy Draft, dual-router attempted, cutover and rollback sequence | 173–174 |
| no-external-build | call-spy | all commands and all verdicts | 172 |

Every recorded fixture MUST include input API payloads/blobs, expected normalized
domain DTOs, expected model call count, expected GitHub mutations, expected
Russian report and Action exit result. Golden reports compare semantic structured
content before rendered whitespace so formatting changes cannot hide a lost
blocker.

Fault injection MUST stop after every persistence, provider-dispatch and GitHub
mutation checkpoint, terminate the process rather than raise inside one call
stack, then rerun the same delivery and a new delivery against the same real
persistence. The provider fixture is external to the application fake and counts
received request IDs. Assertions include exactly zero dispatch before RESERVED,
at most one dispatch per call ID, UNKNOWN_BILLED after the ambiguous window, no
later paid call that Moscow day, zero-call admission, no invented usage/cost, only
authoritative reconciliation transitions, no duplicate Draft/comment, no
unrecorded branch deletion, no unconditional force and stable terminal result.

## 24.20 Implementation work breakdown by dependency

The old implementation-first work breakdown is withdrawn.

Work proceeds only in this order:

1. build the separate executable acceptance harness for the high-risk AC set and
   all 26 findings;
2. prove the harness red against its contract stub for specific semantic reasons;
3. receive formal independent harness-review PASS;
4. implement only the vertical slices in §25.7, keeping every slice executable
   through the external harness;
5. complete integration, smoke, shadow, security and migration gates in §25;
6. request a separate explicit cutover decision.

No product code is authorized by steps 1 through 3.

## 24.21 Specification verdict

Independent formal review completed on 2026-08-27 and the second implementation
RCA completed on 2026-08-28. The latter found that synchronous provider APIs do
not offer the transaction or result lookup needed for unconditional
response-plus-cost durability. Version 1.0.3 closes that implementability gap
without inventing usage: pre-request CAS, at-most-once dispatch,
UNKNOWN_BILLED, current-day paid-work blocking, zero-call admission and only
authoritative reconciliation are now normative. The reviewed product behavior
for event identity, zero-call budgeting, dependency/shared ownership, semantic
verification, lossless TOC/redirect/glossary composition, reports and expiry is
otherwise unchanged.

§23.16 still governs future discoveries: implementation MUST stop and return to
§23 if a new ambiguity would require a product choice.

**Verdict: BEHAVIORAL SPEC PASS. IMPLEMENTATION AND CUTOVER NOT READY.**

---

[Back to Memory Bank index](../../MEMORY_BANK.md)
