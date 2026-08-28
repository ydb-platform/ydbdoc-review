---
type: architecture
date: 2026-08-28
project: ydbdoc-review-ng
status: reviewed-plan
tags: [ng-clean-restart, acceptance-harness, durable-control-plane, migration]
---

# Memory Bank: NG clean restart after three failed implementations

> Part of the [Memory Bank index](../../MEMORY_BANK.md). §23 remains product
> authority. §24 v1.0.3 remains the behavioral, DTO and protocol baseline. This
> section supersedes its failed package topology, legacy reuse allowance,
> implementation order and migration sequence. The original independent evidence
> and executable observations for findings 01–26 are fixed in the
> [§26 authoritative failure ledger](13-ng-failure-ledger.md); §25 summaries do
> not replace that ledger.

## 25. Status and non-negotiable hold

This document is a clean-room restart handoff, not authorization to write product
code.

Current state on 2026-08-28:

- three NG implementation attempts are exhausted and failed independent
  acceptance;
- `src/ydbdoc_review/ng/` and `tests/ng/` are deleted;
- the tracked legacy production tree (`src/`, `tests/`, workflows and packaging)
  has no diff from the peeled recovery commit
  `1f04ab1c71488f53c4ad547c20c7e635d59696ad`; production is restored to that
  legacy baseline and no NG cutover has occurred;
- no deleted NG code or test may be restored or inspected as a template;
- legacy `src/ydbdoc_review` and all three failed histories are negative evidence
  only;
- no NG production cutover is allowed;
- no NG `doc_translate` invocation is allowed, including as a smoke test;
- the first clean-room acceptance harness and its contract stub failed two
  independent Harness RCAs, were discarded in full, and are not a template;
- both attempted clean-room repositories were deleted after the third Stage 1
  failure. No new product repository exists or may be created before formal
  Phase 0 PASS;
- Phase 0 may restart only in a newly created acceptance repository under the
  three-deliverable architecture in §25.4;
- no new product implementation may start before the Phase 0 harness receives
  formal review PASS.

The allowed next work is requirements clarification, fixture preservation,
contract authoring, acceptance-harness implementation and review, plus read-only
provider/YDB/GitHub capability checks. A green unit suite, type checker, linter or
traceability ledger cannot lift this hold.

## 25.1 Exhausted-attempt record

### 25.1.1 Attempt 1: isolated package implementation

The first attempt used an in-repository package below
`src/ydbdoc_review/ng/` and claimed coverage of NG-AC-001..175.

Final verdict: **TEST FAIL**.

Independent RCA confirmed findings #1 through #23 and #25 through #26. Finding
#24 was PARTIAL and none of the 26 findings was refuted. The package boundary and
an AC ledger therefore did not demonstrate behavior through the production
entrypoint.

### 25.1.2 Attempt 2: remediation of the first RCA

The second attempt added preclaim, persisted identity, recovery, semantic
verification, strictness and fixture work inside the same implementation and
test environment.

Final verdict: **TEST FAIL**.

Production-path reproduction ended with:

- CLOSED: #8, #9, #10, #11, #12, #13, #14, #16, #18, #19, #20, #21, #22 and
  #24;
- PARTIAL: #2, #5, #15, #23 and #25;
- OPEN: #1, #3, #4, #6, #7, #17 and #26.

The systemic causes were:

- duplicate handling was not a single durable phase-resume machine;
- fallible dependencies were created before the terminal-report boundary;
- acceptance evidence was self-referential;
- external-effect recovery was not expressed through strict typed records;
- operational identity contaminated semantic verification identity;
- stored and adapter DTO validation was insufficient;
- adapter tests replaced production boundaries with friendly fakes;
- ordinary bilingual verify made zero critic calls;
- internal and external verification of identical semantic bytes produced
  different cache identities.

This RCA also established that synchronous model APIs cannot guarantee atomic
provider response plus local cost persistence. §23 and §24 v1.0.3 therefore
fixed the safe result: durable `RESERVED`, at-most-once dispatch,
`UNKNOWN_BILLED`, no invented usage/cost and authoritative reconciliation only.

### 25.1.3 Attempt 3: final in-tree remediation

The third attempt claimed a durable control plane, typed mutation journal,
`UNKNOWN_BILLED`, a shared semantic verifier, strict DTOs and executable
`tester_item_01` through `tester_item_26` coverage. Its in-tree tests, CLI checks,
Ruff and scoped mypy were green.

Final verdict: **TEST FAIL**.

Those results did not provide the required independent acceptance evidence:

- the harness, implementation and claimed predicates were developed and changed
  in one repository and one review surface;
- the harness was not first demonstrated red against an empty or contract-stub
  implementation;
- the 26 findings were represented by implementation-coupled tests and a ledger,
  not independently frozen black-box observations;
- tests could claim NG-AC coverage without proving the observable predicate;
- real YDB, fresh-process crash recovery and an external provider dispatch spy
  were not mandatory evidence before implementation;
- the high-risk production entrypoint, workflow identity and adapter boundaries
  could still be substituted by fakes;
- the test design did not prevent runtime generation of predicates from the same
  specification it claimed to verify.

The final failure is an acceptance-system failure even where individual
implementation behaviors may have looked correct. No implementation-complete or
cutover-ready claim survives it. The code and in-tree tests were deleted so a
fourth patch cycle cannot inherit their structure.

### 25.1.4 First clean-room harness attempt

The first Phase 0 harness also failed and is exhausted. Its own green selftests
did not protect the acceptance boundary. Two independent Harness RCAs reproduced
a malicious claimant receiving **165/175 AC PASS and 26/26 finding PASS** after
making only one GitHub call, one provider call and one arbitrary persistence
write, then copying public expected-observation strings into candidate output.

Final verdict: **HARNESS TEST FAIL**.

The systemic failures were:

- the public verdict runner executed one disconnected contract probe while the
  scenario executor lived only in selftests;
- one generic “observable”, GitHub, provider or persisted-state presence check
  stood in for many unrelated AC predicates;
- candidate-owned `satisfied`, `finding_evidence` and AC/finding IDs could act as
  self-certification;
- runner, scenario and stub protocol shapes were inconsistent and not validated
  by closed schemas;
- mutants proved that an effect was visible, not that the assigned predicate
  killed the wrong behavior;
- the advertised external YDB profile was not connected to verdict execution;
  a custom HTTP dictionary silently replaced real YDB semantics;
- declared persistence/provider/artifact/GitHub crash windows were not wired to
  durable receipt and response-flushed barriers;
- the eight PR45949 files were internally checksummed, but neither provenance nor
  expected operations were executed as a candidate scenario.

The complete harness, contract stub, generated baseline and test artifacts were
deleted. Both clean-room repositories were recreated empty. Nothing from this
harness may be copied forward except the two RCA reports and the requirements
clarifications recorded in this section.

### 25.1.5 Stage 1 contract-schema attempt 1

The first Stage 1 contract-schema attempt failed independent adversarial review
and was discarded. It incorrectly wrote contract artifacts into the then-existing
runtime repository, violating the then-current pre-PASS hold. The review also
demonstrated that nested arbitrary payloads,
embedded `NG-AC` and finding claims, and production test controls passed the
schemas. External boundaries, real-YDB inspection and health/capabilities
invocation were incomplete, while the meta-tests did not exercise OpenAPI,
conditional constraints or the demonstrated evasions.

Verdict: **STAGE 1 ATTEMPT 1 FAIL**. All generated runtime contents were deleted,
and the historical Stage 1 was restarted only in the acceptance repository. No
artifact from this attempt is a template for the current three-deliverable
Phase 0.

### 25.1.6 Stage 1 contract-schema attempt 2

The second Stage 1 attempt also failed independent adversarial review and was
discarded in full. It corrected repository placement, the first self-certification
evasions, canonical CLI/exit mapping and basic OpenAPI validation; all 57 authored
meta-tests passed. The independent reviewer nevertheless proved that production
configuration still accepted loopback test endpoints, the candidate could submit
its own crash-barrier events, real-YDB inspection was candidate-facing and exposed
only counts/digests rather than independently decoded rows, and one provider
reconciliation response had no closed versioned schema.

Verdict: **STAGE 1 ATTEMPT 2 FAIL**. The acceptance repository was deleted and
reinitialized empty. The subsequent attempt 3 tried to separate candidate
configuration from harness-owned transport, kill and native-YDB observation
contracts, but §25.1.7 records its independent failure and deletion.

### 25.1.7 Stage 1 contract-schema attempt 3 and full restart

The third Stage 1 attempt passed all 65 authored meta-tests and corrected the
previous candidate/harness ownership defects, but it also failed independent
adversarial review. Candidate boundary calls had no current acceptance-session
token. JSON Schema's base64 annotation accepted undecodable bytes and no canonical
validator proved decoded bytes against size/digest. Encoded self-certification
claims passed terminal validation. The production endpoint check accepted URL
userinfo and performed an unpinned one-time DNS lookup, leaving a rebinding gap.

Verdict: **STAGE 1 ATTEMPT 3 FAIL**. The agreed three-attempt limit is exhausted.
There is no fourth patch cycle. Both clean-room repositories were deleted in full.
Two independent RCA analysts subsequently agreed on the simpler three-deliverable
architecture in §25.4: semantic validation, observation ownership and network
pinning belong to harness-owned executable controls, not JSON Schema. Product
implementation, product-repository creation and `doc_translate` remain prohibited
until formal Phase 0 PASS.

### 25.1.8 Acceptance Trust Kernel attempt 1

The first implementation of Deliverable A failed independent adversarial testing.
Its 24 authored tests passed, but reviewer-owned mutations obtained false passes
in all four mandatory risk classes. Capabilities and acceptance destinations were
forgeable, YDB authority was a driver-supplied boolean, nested observation state
and captured-byte evidence were not independently frozen/revalidated, stale
boolean barriers were not bound to a process/request/session identity, and the
gateway's receipt/commit/flush sequence was only in-memory bookkeeping rather
than an authoritative durable transport record.

Verdict: **DELIVERABLE A ATTEMPT 1 TEST FAIL**. The uncommitted attempt cannot be
frozen or used by Deliverable B. Two independent RCAs agreed that its public
dataclass/helper trust model was structurally forgeable and must be discarded,
not patched. A1 was deleted in full. Attempt 2 must be built around one
harness-owned authority, opaque registry handles, an fsync append-only causal
journal, process-correlated one-shot kill permits, deep canonical revalidation
and bundle assembly exclusively from authoritative registry snapshots.

## 25.2 Exact repository and distribution recommendation

Use **two repositories in sequence**, not another subproject in this repository
and not another package in the `ydbdoc_review` distribution.

1. Acceptance repository first: `ydbdoc-review-ng-acceptance`.
   - It is the only repository created during Phase 0.
   - It owns the trust kernel, atomic scenario packs, external observers,
     orchestration, static predicates and the 26-finding ledger.
   - It never imports `ydbdoc_ng` or `ydbdoc_review`.
   - It runs only supplied external executables or OCI images as separate
     processes.
   - Formal harness review is performed without product implementation changes.
2. Production repository after formal Phase 0 PASS: `ydbdoc-review-ng`.
   - Python distribution: `ydbdoc-review-ng`.
   - Import root: `ydbdoc_ng`.
   - Executable: `ydbdoc-ng`.
   - Deployment artifact: one immutable OCI image pinned by digest.
   - It has no source, package, workspace or runtime dependency on this legacy
     repository.
The current `ydbdoc-review` repository remains the legacy production source and
the home of this Memory Bank until migration. Eventual workflow routing may pin
the new OCI digest, but it MUST NOT vendor or import the new distribution.

This sequential two-repository boundary is the exact recommendation. A same-repository
subproject is rejected because it preserves shared test fixtures, editable
imports, coverage incentives and review coupling that allowed all three failed
attempts to claim closure. If repository creation is administratively blocked,
work stops before executable Phase 0 work. A monorepo fallback is not pre-approved.

## 25.3 Clean-room architecture

### 25.3.1 Dependency closure

The production dependency closure contains no `ydbdoc_review` module, wheel,
editable install, copied failed-NG source, plugin, dynamic import, subprocess or
RPC sidecar. There is no legacy allowlist and no `legacy_primitives` bridge.

Third-party Markdown/YAML parsers, GitHub clients, provider SDKs and YDB clients
may be selected directly, pinned and tested as new dependencies. Expected
behavior is derived only from §23, effective §24 rules, frozen external API
contracts and independently authored fixtures. Legacy output may be recorded as
an input comparison but never as an expected result.

The production repository has these logical layers:

~~~text
ydbdoc_ng/
  domain/          frozen values, invariants, semantic hashes
  contracts/       versioned strict ingress/egress DTO schemas
  application/     command state machines and vertical use cases
  verifier/        one semantic verification core
  control_plane/   run ledger, locks, typed outbox, recovery
  ports/           GitHub, model, YDB, artifact, snapshot, clock
  adapters/        new implementations of those ports
  entrypoints/     process assembly only, no policy
~~~

`domain`, `contracts` and `verifier` have no filesystem, environment, clock,
network, git, GitHub, provider or YDB access. Application policy depends on
ports. Adapters implement ports. Entrypoints only validate configuration and
assemble one command worker.

### 25.3.2 Strict DTOs

Every boundary uses a closed, versioned DTO. This includes webhook input,
GitHub responses, provider responses, YDB rows, artifacts, outbox effects,
reconciliation evidence and executable output.

Decoding rejects:

- unknown or missing fields;
- unknown enums or state transitions;
- invalid nullability, ranges, timestamps, decimal cost or repository paths;
- mutable or unordered collections where canonical order is required;
- a size/digest mismatch;
- a stored row whose key disagrees with its value;
- an artifact or response whose declared schema version is unsupported.

No adapter returns an untyped dictionary to the application. Every read from
YDB is decoded as if it came from an untrusted external system. Corrupt state
produces a typed technical terminal result. It never chooses a default transition
or silently drops a field.

### 25.3.3 Durable control plane

The command runtime is one iterative, persisted state machine. It does not resume
by recursively calling a command handler.

1. A minimal ingress validates the event envelope and durably claims
   `(repository, delivery_id)` before GitHub content, provider, snapshot or
   artifact work.
2. The claim stores event PR, command, label timeline event ID, Actions run ID,
   actor, raw-event digest, phase and revision.
3. Source PR and lineage identity are bound in one compare-and-set update before
   active-run comparison or source locking.
4. Each phase has explicit preconditions, durable outputs and one next phase.
5. Duplicate delivery loads the row and resumes the first incomplete phase. It
   never starts a parallel path.
6. All fallible adapter creation and calls occur inside a phase whose failure can
   be rendered as a terminal report. Bootstrap failure before a durable claim
   performs no content or paid effect and leaves a workflow-level technical
   result for retry.
7. One injected clock supplies every completion timestamp used by persistence,
   reports, audit and expiry.

The control store is the authority. GitHub Actions run-name is provisional
display metadata, never lineage identity or a lock.

### 25.3.4 Typed outbox and recovery

Every GitHub mutation is a closed typed outbox effect, for example:

~~~text
RemoveCommandLabel
CloseOwnedDraft
DeleteOwnedBranch
PushNewBranch
CreateDraft
ForcePushWithLease
UpdateDraftMetadata
UpsertCanonicalComment
~~~

Every effect contains its schema version, deterministic effect ID, run/phase,
exact target, expected remote precondition, desired digest, created time and
typed reconciliation rule. The state machine is:

~~~text
PLANNED -> INTENT_RECORDED -> DISPATCH_STARTED -> REMOTE_CONFIRMED
DISPATCH_STARTED -> AMBIGUOUS -> RECONCILED
~~~

Only the outbox dispatcher performs external writes. Recovery reads the remote
system and the typed effect. It may confirm the desired effect, repeat a proved
idempotent effect, or block on conflict. It never interprets a free-form payload,
deletes an unrecorded branch, overwrites human bytes or uses unconditional force.
Command-label reconciliation binds the exact timeline event so a newer label is
never removed by an older delivery.

Provider requests are not generic outbox effects. They use the stricter model
call state machine below because remote acceptance and billing may be
unobservable.

### 25.3.5 Model calls and `UNKNOWN_BILLED`

For deterministic `call_id`, the state transitions are exactly:

~~~text
ABSENT -> RESERVED -> RESULT_RECORDED
RESERVED -> UNKNOWN_BILLED -> RESULT_RECORDED
UNKNOWN_BILLED -> RECONCILED_NOT_BILLED
~~~

`RESERVED` is committed before a request can leave the process. The transport
performs at most one wire dispatch and disables SDK, HTTP middleware and proxy
retries. A returned response artifact, usage and actual cost are persisted before
parsing or downstream use.

After a crash in the ambiguous window, recovery converts `RESERVED` to
`UNKNOWN_BILLED` and does not redispatch. Cost and usage remain null, not zero.
New paid calls are blocked for the reservation Moscow day, while proven zero-call
work remains admissible. Only provider evidence tied to the exact call/request
identity can record the result or prove non-acceptance/non-billing. Human input,
timeout interpretation, local absence and an estimated tariff cannot clear it.

### 25.3.6 One semantic verifier

Translate, continue and verify construct the same strict `VerificationCase` and
call one verifier service.

- An ordinary bilingual pair is one critic subject containing both exact byte
  strings. The harness observes one critic unit and the specified attempt count.
- The semantic case hash includes checked bytes, directions, scopes, decisions,
  rule/prompt versions and selected model identifiers.
- It excludes run ID, delivery ID, Actions ID, timestamps, report location,
  branch, PR number and publication commit.
- Publication adds a separate binding envelope from the unchanged case hash to
  the exact Draft commit and actual bytes.
- Internal verification and later verification of byte-identical reconstructed
  Draft content reuse the same stored critic result.
- A manual byte change, scope change, rule/model/prompt change or expired record
  is a cache miss.

The deterministic validator runs before model work and produces stable rule IDs,
evidence digests and order. The critic never repairs. Repair is an application
step followed by a new complete verification pass.

## 25.4 Phase 0: independent executable acceptance harness

Phase 0 is the first deliverable. It is not a test folder added after a package
skeleton. No production repository exists through formal Phase 0 PASS. Only the
acceptance repository is created during Phase 0. A product README, license,
package skeleton, contract copy or executable before PASS is unauthorized.

Three independently reviewed vertical deliverables replace the failed five
horizontal stages. Each deliverable has a pinned commit and review decision; a
later deliverable cannot receive PASS until its upstream is frozen:

1. **A, trusted executable conformance kernel.** This harness-owned kernel fixes
   the threat model, ownership matrix, closed versioned schemas and executable
   semantic validators for session authentication, decoded bytes, digests,
   terminal/audit text and resolved endpoints. JSON Schema proves shape only.
   It never proves byte semantics, observation provenance, DNS routing or a
   verdict. A implements and independently verifies the session issuer,
   harness-owned gateway and boundary spies, native real-YDB observer component
   and interface, external process supervisor and immutable ObservationBundle
   builder. The kernel includes adversarial probes for every bypass from all
   three deleted Stage 1 attempts.
2. **B, atomic scenario packs.** Every finding and high-risk AC is frozen as one
   content-addressed pack containing fixture bytes and provenance, launch input,
   required harness-owned observations, static predicate source, executable
   assigned must-kill mutants and expected RED/BLOCKED/PASS reasons. Fixture,
   predicate and mutant cannot be reviewed or changed independently. Finding 01,
   including the malicious claimant, is the mandatory pilot and must receive an
   independent PASS before the remaining packs are authored.
3. **C, orchestrator and pinned integrations.** The sole verdict path executes
   the complete frozen B manifest by binding the already independently reviewed
   A kernel to pinned real integrations. It adds no alternative gateway, spy,
   YDB-observer, supervisor or ObservationBundle implementation. Through that
   single verdict path it runs separate processes, real disposable YDB, pinned
   gateway/network controls and every required barrier and kill/restart, then
   produces the signed pinned H/S/C/D artifact.

The exact reopen rules are:

- any A change reopens A and invalidates all B packs and C;
- changing one B pack reopens that pack, the aggregate B manifest and C, but not
  independently frozen unchanged packs;
- changing a shared content-addressed blob creates a new digest and reopens every
  pack that references it;
- a C-only change reopens C unless it changes an A protocol or B semantics, in
  which case the corresponding upstream deliverable also reopens;
- an independent reviewer, never the author alone, classifies the affected scope;
- every reopened upstream requires a complete rerun of its affected downstream.

### 25.4.1 Black-box target contract

The harness launches an OCI image or `ydbdoc-ng` executable in a fresh process.
It supplies only versioned external inputs:

- a command event JSON file or webhook request;
- environment/configuration by documented names;
- external GitHub and provider spy endpoints;
- a disposable real YDB database and artifact endpoint;
- a frozen clock/fault schedule through an explicit test-only process contract;
- an output directory for the versioned terminal result and audit export.

The harness observes exit status, stdout/stderr schema, HTTP calls, provider
request IDs, YDB rows, artifact bytes/digests, process restarts and GitHub state.
It imports no production module and does not call internal functions.

The threat model treats the candidate as intentionally hostile. It knows every
public schema and may emit arbitrary terminal/audit text, encode forbidden claims,
replay or cross-wire sessions, follow redirects, exploit URL userinfo or DNS
rebinding, forge counts/digests/barriers and write plausible but semantically
wrong rows. It may exit or be killed at every external-effect window. Candidate
output, candidate-computed summaries and candidate-selected endpoints are never
trusted observations.

Ownership is exclusive:

- the harness owns session identities and secrets, listeners, gateway and DNS
  policy, raw transcripts, receipt/commit/response-flushed barriers, process
  kill/restart, direct YDB inspection, byte decoding/digests, ObservationBundle
  construction and every verdict predicate;
- external services own their independently recorded receipt and flush facts;
- the candidate owns only its documented requests and its terminal/audit product
  output, both treated as untrusted input;
- schemas own shape only; executable A validators own local semantic validity;
  C owns proof through the actual pinned boundary.

The orchestrator is the only owner of scenario execution and verdict input. For
each scenario it emits an immutable `ObservationBundle` containing at least:

- harness commit and scenario/fixture digests;
- exact candidate or stub OCI digest and process lifecycle;
- strict terminal/audit decode result;
- independently recorded GitHub, provider and artifact transcripts;
- independently queried real-YDB rows and transaction observations;
- barrier receipt/response-flush records, kill PID/cgroup and restart identity;
- frozen time inputs, exit status, stdout/stderr digests and timeout result.

The candidate cannot write, replace or name fields inside `ObservationBundle`.
Every scenario receives an independently generated unpredictable session token
with at least 256 bits of entropy through a launch-secret channel. Every
GitHub/provider/artifact spy request carries it in one fixed authentication
header. It is forbidden in URL, request body, terminal/audit output and logs.
The listener validates it before recording an observation. Missing, stale,
unknown and cross-session tokens are rejected and do not count. The candidate
has no observation or barrier-ingestion API: receipt, commit and complete-response
flush are derived only by the harness-owned listener. The target network can
reach only the harness endpoints for its session and cannot read fixture
expectations, predicates, other scenario state or harness source.

Raw captured bytes are the content-addressed authority. Their manifest records
source identity, exact length and lowercase SHA-256 calculated by the harness.
When a protocol transports bytes as base64, A requires strict RFC 4648 standard
or URL-safe form, one declared alphabet, canonical padding, no whitespace,
decode/re-encode equality, exact decoded size and an independently recomputed
digest. JSON Schema `contentEncoding` alone proves nothing. If an upstream API
cannot provide raw response bytes, the only fallback is explicitly tagged
canonical JSON using one pinned canonicalization version; both the parsed value
and canonical bytes/digest are retained, and the fallback cannot masquerade as a
raw capture. Duplicate JSON keys and invalid UTF-8 are RED.

Candidate-controlled text is zero evidence. A first scans NFKC/case-folded raw
text for reserved `NG-AC-[0-9]+` and frozen finding-ID patterns. It then examines
only separate maximal ASCII base64-like tokens of length at least eight, using
one strict standard or URL-safe alphabet, canonical padding and no mixed
alphabet. An unmarked token is decoded once and inspected only when it is valid
UTF-8 printable text; `TkctQUMtMDAx` therefore decodes to `NG-AC-001` and is RED.
Recursion is allowed to depth three only for an explicitly marked
`base64:<token>` chain where every next layer has the same marker. Other unknown
encodings are inert zero-evidence, not promised RED and never PASS.

Production and acceptance endpoint policies are disjoint. Production accepts
only trusted production configuration and rejects URL userinfo in literal or
percent-encoded form plus private, loopback, link-local, multicast, reserved and
metadata destinations. Acceptance endpoints are never valid production config:
the candidate may use only harness-issued, unpredictable, session-bound endpoint
capabilities inside the isolated namespace and harness-owned gateway. No ordinary
candidate-supplied URL can opt into the acceptance policy.

For both policies A creates a harness-owned `ResolvedEndpoint` recording original
URL or capability, canonical host, complete resolved address set, DNS transcript,
time and policy digest. C forces every actual connection through the reviewed
gateway to the pinned address while checking hostname, SNI and peer address.
Later DNS answers cannot change the destination. Redirects repeat the applicable
policy and cannot escape their original authority or session capability. Mixed
public/private production answers, public-then-private rebinding, peer mismatch,
userinfo and cross-session acceptance endpoints are mandatory RED probes; a
one-time resolver check is never sufficient.

YDB has no candidate-facing inspection protocol. The harness uses a separate
least-privilege inspection identity and the real pinned driver to decode exact
typed rows, transaction outcomes and ordering. It computes row and aggregate
digests itself from a pinned typed canonicalization. Candidate-provided row
counts, rows or digests cannot satisfy a predicate.

The test-only process contract is limited to external clock input and boundary
fault orchestration. It MUST NOT expose internal phases, classes, storage keys or
implementation-selected hook names. Process death is initiated by the harness
from outside the target after an independently observed network, YDB, artifact
or GitHub boundary event. The same release image used for acceptance and eventual
cutover is exercised; production configuration rejects every test-only endpoint
and control, and no target behavior may branch on a test/finding identifier.

### 25.4.2 Mandatory red baseline

The acceptance repository contains its own `contract-stub` executable. The stub
accepts the launch protocol, emits `NOT_IMPLEMENTED`, makes no external effects
and exits non-zero.

Before product coding, CI MUST prove:

1. the harness itself launches and completes against the stub;
2. every high-risk behavioral test and every finding #1..#26 fails for its own
   expected semantic reason, not because the binary is missing;
3. the stub makes zero hidden provider/GitHub effects;
4. seeded faulty targets or harness-owned fault switches demonstrate that each
   critical assertion can distinguish at least one plausible wrong behavior;
5. removing or inverting a critical assertion makes the harness review fail.
6. the malicious claimant from the two Harness RCAs obtains zero AC PASS and zero
   finding PASS;
7. `satisfied`, `finding_evidence`, AC IDs and finding IDs in candidate output are
   protocol RED under the bounded raw/base64 policy in §25.4.1 rather than
   accepted as evidence;
8. after authored tests pass, an independent reviewer supplies at least one new
   mutation in each active threat class: ownership/session, canonical bytes,
   encoded claims, endpoint/DNS and observed persistence. A mutation incorporated
   into authored fixtures before review does not count as reviewer-owned.

A single global assertion such as “implementation unavailable” is not evidence.
Each test records the missing observable that kept it red.

### 25.4.3 Static predicates, not generated predicates

Acceptance assertions are ordinary reviewed source code and frozen golden data.
The harness MUST NOT at runtime parse §23, §24, this document, an NG-AC table or
implementation metadata to create expected predicates, expected call counts,
test IDs or PASS rows.

A static traceability file may link a test to requirement IDs for navigation.
That file is non-executable metadata and cannot satisfy coverage by itself.
Expected DTOs, reports, mutations and state transitions are independently
authored. Updating a predicate or golden requires a requirements-review reference
and a harness-only review before an implementation may rely on it.

One `ObservationBundle` may support several predicates only when each predicate
selects distinct frozen fields proving its own behavior. Presence of the bundle,
a terminal result, a call, a row, a restart or copied expected text is never a
predicate. A high-risk AC or finding with no dedicated scenario and observed-state
predicate is BLOCKED, not RED and never PASS.

### 25.4.4 High-risk acceptance set

Phase 0 implements executable tests at least for:

- event, label, ACL, budget, delivery, active-run and lock gates,
  NG-AC-009..027;
- exact manifest pagination, immutable snapshots, mixed pair operations and
  corrupt DTO rejection, NG-AC-028..044;
- lineage, continue-comment races, destructive restart and expiry,
  NG-AC-045..064;
- model at-most-once dispatch, `UNKNOWN_BILLED`, fallback and call identity,
  NG-AC-019..022, NG-AC-090..105, NG-AC-169 and NG-AC-171;
- one paired bilingual critic subject and semantic internal-to-published cache
  reuse, NG-AC-142..146;
- safe bundle composition and no partial publication, NG-AC-151..154;
- GitHub mutation journal, read-only verify and terminal reporting,
  NG-AC-155..165 and NG-AC-170;
- real persistence, TTL, Moscow midnight and fresh-process recovery,
  NG-AC-166..171 and NG-AC-175;
- one-writer migration and rollback, NG-AC-173..174;
- the recorded eight-file PR 45949 manifest/TOC/redirect regression.

Every listed high-risk AC has its own frozen scenario identity even when scenarios
share byte fixtures or service setup. A scenario manifest explicitly maps the AC
to its input digest, required observations, assigned mutants and predicate source
digest. Range-generated predicates and one function assigned to multiple AC IDs
are forbidden.

The remaining NG-AC criteria may be added by later harness-only increments, but
all NG-AC-001..175 must pass before cutover.

### 25.4.5 The 26-finding gate

The acceptance repository has exactly 26 independently reviewed finding modules,
`finding_01` through `finding_26`. Each contains:

- the original finding statement and source review reference;
- the §23/§24 rule it threatens;
- frozen input fixture provenance and digest;
- externally observable expected calls, state, report and exit result;
- one negative control demonstrating a plausible false PASS is detected;
- links to, but no executable dependence on, related NG-AC tests.

Each module owns a scenario fixture and evaluates only harness-observed remote,
persistence, artifact, process and strictly decoded result state. Candidate text
that repeats the required observation is not evidence. A shared global call or row
cannot satisfy any finding unless that exact call/row shape and its causal order
are the finding's frozen predicate.

The source statement, reproduction, observed bad output, required good
observation, negative-control mutant, provenance and closure history for every
module come from [§26](13-ng-failure-ledger.md). The harness may copy reviewed
fixtures from that ledger into static test-owned data, but MUST NOT parse the
ledger or §24 at runtime to manufacture its assertions.

Phase 0 cannot pass with placeholder names, empty predicates, generated tests or
only a CLOSED/PARTIAL/OPEN ledger. The full original text and expected
observation for all 26 findings must be recovered from the independent review
record. It must not be reconstructed from deleted implementation or tests.

### 25.4.6 Effective architecture predicates

The old topology wording of NG-AC-001..003 is replaced without renumbering:

- **NG-AC-001 effective:** the production artifact is built only from the new
  `ydbdoc-review-ng` repository and exposes the `ydbdoc_ng` distribution plus
  one versioned executable contract.
- **NG-AC-002 effective:** source, lockfile, wheel and OCI scans reject any
  `ydbdoc_review` dependency or legacy/failed-NG import, copy, plugin, subprocess
  or sidecar closure.
- **NG-AC-003 effective:** there is no legacy symbol allowlist. Behavior reuse is
  fixture evidence only, and acceptance expectations never come from legacy
  results.

The acceptance repository separately proves that it has no production or legacy
imports and that production packaging cannot include acceptance code.

The atomic-pack mutation gate is exact:

- every finding and every high-risk AC has at least one executable assigned
  must-kill mutant;
- claimant, disconnected-executor, protocol-mismatch, generic-presence,
  emulator-fallback, skipped-barrier and wrong-PR45949-provenance mutants are
  mandatory global controls;
- the relevant scenario/predicate must turn each assigned mutant RED for the
  intended missing observation, not for startup failure or an unrelated schema
  error;
- the must-kill score is exactly 100 percent. One surviving assigned mutant keeps
  Phase 0 BLOCKED;
- mutation assignments and results are content-addressed parts of the review
  artifact. Unassigned mutants and aggregate percentages cannot hide a survivor.

### 25.4.7 Formal harness review PASS

Product coding remains prohibited until an independent reviewer records all of:

- PASS: repository separation and black-box process boundary;
- PASS: red contract-stub run for the high-risk set and all 26 findings;
- PASS: static independently authored predicates and fixture provenance;
- PASS: external GitHub/provider spies and real disposable YDB orchestration;
- PASS: process-kill testing at every provider, persistence and mutation window;
- PASS: response-flushed barrier evidence and 100 percent assigned must-kill
  mutation score;
- PASS: no implementation code or implementation-derived golden data was used;
- PASS: reviewed traceability with no criterion satisfied by a name/ledger alone.

Formal PASS is one signed, pinned **H/S/C/D** artifact:

- **H**: exact harness orchestrator commit/tree SHA and mutation-result digest;
- **S**: complete atomic scenario-pack manifest digest, including every fixture,
  provenance record, predicate and assigned-mutant result;
- **C**: trusted conformance-kernel and closed-schema bundle digest;
- **D**: pinned dependency/integration lock containing every OCI image digest,
  real-YDB profile identity and external service protocol version.

The artifact also records the A, B and C review PRs/approvals, exact contract-stub
red run, malicious-claimant zero-pass run, reviewer-owned mutation results,
barrier evidence and reviewer signature. An unpinned `latest`, mutable profile,
missing deliverable approval or digest mismatch makes the artifact invalid. Any
later change follows the exact reopen rules in §25.4 and requires a new full
H/S/C/D review after all affected downstream work reruns.

## 25.5 Integration prerequisites

### 25.5.1 YDB

Before the durable-control-plane slice starts:

- provision a dedicated disposable YDB database or database prefix unavailable
  to legacy production;
- verify compare-and-set or serializable transaction semantics with two real
  processes, not an in-memory repository;
- verify unique keys, indexes, decimal/timestamp precision and strict row decode;
- verify application-level expiry at `expires_at <= now` independently of
  delayed physical TTL deletion;
- prove Moscow-day queries across midnight with the injected clock;
- run crash/restart tests against the same persistent database;
- provide least-privilege credentials and an inspection identity for the harness;
- define schema migration forward/rollback rules before the first persistent
  production-shaped row exists.

Phase 0 verdict execution always uses a real disposable YDB instance pinned by
OCI digest or a pinned external profile recorded in **D**. An in-memory store,
SQLite substitute, custom HTTP dictionary or emulator fallback may be used only
for non-verdict developer feedback and cannot satisfy or unblock a predicate.
Missing credentials, unavailable real YDB, profile mismatch or readiness failure
is BLOCKED. It is never converted to an emulator run or PASS.

The orchestrator independently applies the pinned schema, waits for readiness,
queries rows with the real YDB driver and proves CAS with separate processes. A
harness-owned transport observer or transaction-aware proxy records both request
receipt and committed-response release where a crash barrier requires it. The
candidate cannot select the YDB profile.

### 25.5.2 Model providers

Before the model-control slice starts, record per provider/model:

- the exact endpoint and API/schema version;
- whether client request IDs are accepted and returned;
- whether authoritative request/result/billing lookup exists;
- returned usage fields and the versioned tariff needed for actual RUB cost;
- timeout and cancellation semantics;
- every SDK, transport, proxy and platform retry, all disabled for the one-attempt
  adapter;
- staging credentials, quota and a safe explicitly authorized contract-call plan;
- redaction rules for provider IDs, errors, headers and transcripts.

Lack of authoritative lookup is allowed and yields lasting `UNKNOWN_BILLED`.
Hidden transport retries, inability to observe wire dispatch, or inability to
persist request identity before dispatch blocks that provider adapter.

The harness provider is an external HTTP service that counts received call IDs
and can hold or release each request and response at observable transport
barriers. The harness, not provider or target code, kills the target process at
those barriers and then restarts the same release image against the same YDB.
The evidence separately records durable request receipt, response construction,
complete response flush and client-connection outcome, correlated by unpredictable
session and request IDs. Required ambiguous windows kill only after their exact
response-flushed barrier. A request-received kill cannot stand in for a
response-returned ambiguity. The observer does not claim to see an unobservable
instruction between socket completion and local persistence. It is not an
application fake.

### 25.5.3 GitHub and artifacts

Before publication work:

- create a private fixture repository with a dedicated bot and least-privilege
  token separate from YDB docs production credentials;
- record webhook, timeline pagination, PR-files pagination and Actions identity
  payloads from the supported GitHub API version;
- prove force-with-lease, bot-owned Draft identity and conflict behavior;
- expose separately correlated mutation-received, remote-state-committed and
  response-flushed barriers;
- provision an isolated artifact namespace with digest verification, TTL,
  encryption and secret-redaction checks;
- ensure the harness can inspect all remote effects without using product
  internals.

The PR45949 fixture freezes raw paginated API responses and headers, capture tool
version, base/head/merge/current identities, every blob digest and an independent
provenance verification. Its scenario serves those exact pages/blobs and observes
candidate results through remote publication state: eight exact overlay paths,
DELETE plus ADD rather than MOVE, redirect evidence, TOC operations and atomic
dependency. Merely counting eight unique strings is forbidden. A two-file,
wrong-path, unpaginated, mutable-current or digest-mismatched fixture is a
must-kill mutant.

No production YDB docs write credential is available to the new runtime during
implementation or shadow testing.

## 25.6 Kill criteria

Stop the current slice and invalidate the implementation attempt if any of these
occurs:

- product code is written before formal Phase 0 harness-review PASS;
- acceptance is moved into the product tree or imports production internals;
- the public verdict path does not execute the complete frozen scenario manifest;
- candidate `satisfied`, `finding_evidence`, AC/finding IDs or copied expected
  prose can influence PASS;
- a predicate/golden is weakened to match implementation without prior
  requirements and harness-only review;
- any runtime predicate is generated from specification or traceability text;
- any `ydbdoc_review` or failed-NG artifact enters the source, build, wheel, OCI,
  plugin, subprocess or service dependency closure;
- duplicate delivery can enter a second orchestration path rather than resume a
  persisted phase;
- a GitHub effect exists without a typed durable intent and reconciliation rule;
- a provider request can leave before `RESERVED`, can be retried implicitly, or
  an ambiguous call can be represented as zero cost;
- a corrupt/unknown DTO can reach policy through a default or partial decode;
- semantic cache identity includes an operational identifier or identical
  internal/published bytes cannot share one semantic result;
- a high-risk test or any finding #1..#26 regresses at a slice gate;
- any assigned must-kill mutant survives or fails for an unrelated reason;
- real pinned YDB/new-process evidence is replaced by or falls back to an
  emulator, in-memory store or custom HTTP dictionary for formal PASS;
- a required response-flushed barrier is absent, uncorrelated or replaced by a
  request-received event;
- an H/S/C/D digest, pin, staged approval or signature is missing or mismatched;
- a new product ambiguity is resolved in code instead of returning to §23;
- more than one command router can write the command labels;
- a credentialed smoke performs an unplanned content, branch, Draft or model
  mutation.

On a kill, preserve only the failure report, black-box inputs/outputs, audit
export and requirement clarification. Do not promote implementation code into a
new template. The whole implementation attempt stops: no patching, replacement
repository or next slice is authorized. A new attempt requires an independent
RCA, any needed §23 clarification, a harness-only remediation and repeat formal
Phase 0 PASS, followed by explicit human authorization. This gate cannot be
satisfied by renaming a branch, repository, slice or release.

## 25.7 Vertical slices after Phase 0

Each slice produces a runnable OCI image and is tested through the external
harness. A slice does not begin until the prior slice's applicable high-risk and
26-finding tests pass.

### Slice 1: ingress and terminal control

Implement strict event/config DTOs, delivery preclaim, phase CAS, duplicate
resume, injected clock and terminal result/report for zero-content commands. No
model, snapshot, branch or Draft capability exists.

### Slice 2: YDB control plane and typed outbox

Implement real YDB schemas, locks, phase persistence, typed GitHub effect rows,
reconciliation and fresh-process recovery. Exercise label/comment effects only in
the fixture repository.

### Slice 3: model-call safety

Implement provider ports, durable `RESERVED`, at-most-once wire dispatch,
`UNKNOWN_BILLED`, authoritative reconciliation, Moscow-day budget and artifact
digests. No generated content can publish.

### Slice 4: semantic verifier

Implement strict `VerificationCase`, deterministic rules, one paired bilingual
critic subject, semantic hashing, result persistence and publication binding.
Prove translate-like internal verification and external Draft verification reuse
one result for identical bytes.

### Slice 5: immutable manifest and scope

Implement GitHub pagination, official merge identity, exact-SHA snapshots,
operation expansion, pair classification, single-language/unsupported filters and
dependency closure. Pass PR 45949 and corrupt-response fixtures.

### Slice 6: candidate planning

Implement documents, companions, links, images, TOC, redirects and glossary as
immutable typed bundle operations. No GitHub write adapter is linked into this
slice. Prove safe-bundle composition and deterministic omission.

### Slice 7: publication

Connect verified safe bundles to the typed outbox for fixture-repository branch,
Draft and canonical comment effects. Prove lease conflicts, partial crashes,
manual changes and no unrecorded compensation.

### Slice 8: command composition

Compose translate, continue and read-only verify state machines, Russian reports,
lineage expiry, three-continue enforcement and model rotation. Run the full 175
AC suite and all 26 findings.

### Slice 9: credentialed no-content smoke and shadow

Complete §25.8, then run read-only recorded-case comparison. Legacy results are
diagnostic only. The new runtime still has no production content-write routing.

### Slice 10: migration rehearsal and cutover candidate

Perform one-writer, credential-removal and rollback rehearsals against the
fixture environment. Only an independently approved release becomes a cutover
candidate. The prohibition remains until the explicit cutover decision.

## 25.8 Credentialed no-content smoke

The smoke uses real staging YDB, artifact and GitHub credentials plus the exact
release OCI digest. It does not use `doc_translate` and must be incapable of
content mutation.

Required cases:

1. an exact labeled-event fixture with unsupported-only or single-language-only
   scope proves metadata-only zero-call admission;
2. an open fixture PR runs read-only `doc_verify` on a deterministic zero-model
   case;
3. duplicate delivery resumes the same terminal row;
4. a configuration/adapter failure produces the expected technical job result
   without branch, Draft, content or provider activity.

Required observations:

- exact event and Actions identity are persisted;
- only the expected command label/comment effect occurs in the fixture repo;
- provider spy and real provider audit show zero requests;
- no branch, commit or Draft is created or changed;
- YDB phases, outbox rows, audit fields and artifact digests are strict and
  complete;
- secret sentinels do not appear in logs, reports, rows or artifacts;
- a second process reaches the same terminal result without a duplicate effect.

Any content or paid call is a kill event, not a smoke warning.

## 25.9 Migration, cutover and rollback

### 25.9.1 Preconditions

Cutover remains prohibited until all are independently PASS:

- Phase 0 harness review and unchanged critical assertions;
- NG-AC-001..175 and finding_01..finding_26 against the release image;
- real YDB multi-process crash and TTL evidence;
- provider at-most-once and ambiguous-window evidence;
- fixture GitHub mutation and rollback rehearsal;
- credentialed no-content smoke;
- read-only recorded cases including PR 45949;
- security, dependency-closure, secret and OCI provenance review;
- explicit human approval of the release digest and cutover window.

The legacy recovery reference is the annotated tag `pre-ng-2026-08-27`, whose
peeled commit MUST equal `1f04ab1c71488f53c4ad547c20c7e635d59696ad` during
rehearsal and immediately before cutover. The tag is immutable and MUST NOT be
moved. The rehearsal snapshots the verified commit and deployable artifact
digest; rollback uses those recorded immutable values, not a fresh mutable tag
lookup or the then-current legacy branch.

### 25.9.2 Migration rehearsal

1. Inventory legacy open/closed translation Drafts and deterministic branches
   read-only.
2. Export no legacy policy or hidden runtime state into NG. Only authoritative
   GitHub state and §23-approved lineage inputs are eligible.
3. Use a new YDB table/database namespace and artifact prefix. Do not migrate
   failed-NG rows as valid state.
4. Prove legacy Drafts cannot continue as NG lineage. Eventual verify treats them
   as ordinary PRs.
5. Exercise clean-restart discovery only in the fixture repository, including
   duplicate, human-owned and ambiguous branches.
6. Remove production write credentials from the candidate and prove shadow mode
   cannot mutate labels, comments, branches or PRs.
7. Rehearse enable/disable with exactly one writer assertion before and after
   every routing change.

### 25.9.3 Atomic cutover

When separately authorized:

1. disable all three command-label workflows and new label intake;
2. drain or cancel legacy runs and prove no live writer or lock remains;
3. snapshot workflow configuration, credentials and release digests;
4. remove legacy writer credentials before granting the new router credentials;
5. route all three commands atomically to the one pinned NG image;
6. prove exactly one writer, then run only the approved no-content canary;
7. re-enable label intake gradually and monitor control-plane/outbox invariants;
8. do not run a translation command merely to prove deployment.

### 25.9.4 Rollback

1. disable label intake and revoke NG writer credentials first;
2. drain/cancel NG runs and reconcile every lock, outbox effect and model call;
3. preserve NG rows, artifacts and Drafts for audit. Do not reinterpret or
   auto-mutate them through legacy;
4. restore the recorded legacy artifact digest built from the verified peeled
   recovery commit only after proving the NG writer is unable to mutate;
5. grant credentials to exactly one legacy router;
6. record release digest, reason, affected run/lineage/effect IDs and any
   `UNKNOWN_BILLED` calls;
7. require manual handling for all NG-created Drafts and ambiguous branches.

Rollback never imports legacy policy into NG and never permits dual writers.

## 25.10 Readiness verdict

The clean restart plan is complete enough to create only the acceptance
repository and begin Phase 0 deliverable A.

It is **not** product-implementation-ready, smoke-ready or cutover-ready. Those
states require their explicit gates above. The next work is the trusted executable
conformance kernel in a newly created acceptance repository. Deliverable B starts
only after independent A PASS and begins with the atomic finding-01 malicious-
claimant pilot. The next complete Phase 0 outcome is the signed pinned H/S/C/D
artifact after A, B and C review, with the complete high-risk and 26-finding suite
RED against the contract stub for specific semantic reasons, every assigned
mutant killed and zero PASS for the malicious claimant. Only then may the product
repository be created.

**Verdict: CLEAN RESTART PLAN PASS. PRODUCT CODING BLOCKED BY PHASE 0.**

---

[Back to Memory Bank index](../../MEMORY_BANK.md)
