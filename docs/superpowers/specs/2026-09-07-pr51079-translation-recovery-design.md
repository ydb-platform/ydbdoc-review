# PR 51079 translation recovery design

Approved approach: seven sequential implementation tasks, each followed by independent tester acceptance. The approved analysis is `/private/tmp/pr51079-analyst-report.md`. This specification is sufficient to execute the work without that temporary report.

## Problem and evidence

GitHub Actions run 34103571850 used action df602555a2fe60a1f8652debfb21e5df872a3634, translated six Markdown pairs, then withheld publication for caching-authentication-results.md and its two user-token include fragments. The run made 136 translate call starts; eleven empty completions each consumed 8000 completion tokens. Glossary took 1901.4 seconds, auth_config 620.1 seconds, authentication 1418.0 seconds, and caching 128.8 seconds. The two protected Mermaid assets each took 0.1 seconds and made no translation calls.

Navigation discovery admitted security/toc_p.yaml, but `navigation.toc._merge_toc_tree_nodes` took the href branch for authentication.md and never visited its children. Caching was already in translate scope. `_serialize_toc_node` also drops a parent's href when serializing children, so repairing recursion alone is insufficient. `merge_navigation_pair` then returned a clean verdict for unchanged bytes despite blocking scope warnings. With no pending sidebar, final TOC reachability correctly lacked caching. R-GL-16 therefore correctly refused to bootstrap include reachability from this orphan parent.

The earlier run 33997579873 had eight Markdown paths and zero navigation paths. Its scope-discovery failure and the current mixed-node merge failure are separate defects. The include-closure unit fixture supplied an already-correct flat pending sidebar and did not test this composed production path.

Cost comes primarily from the current contract: TranslateStep intentionally sends every source prose segment through full translation and ignores differential configuration. Existing EN, required source coverage, and previously completed translations are not safe durable reuse inputs today.

## Global Constraints

- Python >=3.11; use existing dependencies and the existing Markdown/YFM parser, protection, renderer, navigation and validation machinery.
- Deliver exactly one implementation task to the developer at a time; an independent tester must accept its exact commit before the next task starts.
- Every task uses behavior-level failing tests first, records RED and GREEN, and ends with a scoped commit and tester report.
- Do not move tags, push remotes, mutate GitHub labels or run production workflows during implementation tasks.
- Do not run PR 51079 in production until all seven task tester gates and the aggregate local acceptance gate pass.
- Preserve frozen H0/H/B/R source authority, exact pending overlays, publication leases and fail-closed unsafe/incomplete gates.
- Do not add unrelated source content, change authority mode implicitly, or translate later RU-only PR 52355 as part of this recovery.
- Do not weaken R-GL-16, use ordinary links as include evidence, seed arbitrary pending Markdown as TOC roots, or exempt an entire assets directory.
- Preserve existing accepted EN bytes outside proven edit units; uncertain mappings must be reported and fall back conservatively.
- Persist no secrets or credentials; checkpoint keys and manifests contain public document content, hashes and nonsecret execution identity only.
- Preserve user-owned dirty and untracked files; stage only the exact files assigned to the current task.
- No subagents are spawned by the task developer; the controller owns developer/tester dispatch and task sequencing.

## Authority and content obligations

H0 is landed source parent `0aa50f3ab4688eb53d04888eba9fb3c36968ff14`, PR 50704. H is landed source commit `673b924813e35646535e20d917b014094bf7de14`, PR 51079. B is frozen upstream baseline for the new job. R is the selected RU authority: H for source-preserving mode, B for current mode. These values are immutable job inputs. Verified candidate K and any repair K2 are outputs subject to existing lease and visibility rules, never alternative source inputs.

The exact B of failed run 34103571850 is absent from the supplied log. Use ops provenance if available; otherwise identify a reproduction snapshot explicitly. The analysis inspected `60b6e2c8f3c3ad279c3d1743440ee8ca73aa3e72`, which is not asserted to be the failed run's B.

`RuAuthority.ru_base_sha` is B when R=B. Thus B→B being empty must never establish EN coverage. H0→H represents the API source change, while the source represented by existing EN is a third independent fact. Receipts or validated translation lineage must establish it.

PR 51079 changes only auth_config.md and authentication.md. Its landed parent PR 50704 already adds caching, two includes and the glossary user-token section. These are necessary dependency obligations, including when R=H. Glossary EN exists but lacks user-token; caching and both assets have no existing EN. Keep the six-file dependency closure and one sidebar. Do not admit authorization.md merely because PR 50704 also changed it.

Current EN auth_config and authentication were updated by PR 52330, commit c85c9d084a3b315ca00c70099fd65214a7fe5bf8, from source-preserving PR 40385. Preserve their accepted EN repairs. That lineage does not prove coverage of PR 50704 or 51079.

Later RU-only PR 52355, commit b13cac5dda249992dd70020f1a7e79f82e4efaf7, adds code formatting to parameter names in auth_config.md. Its 63 added/63 removed lines are separately attributed H→B drift, outside this recovery's documentation changes. Production authority selection is a controller decision; neither a reuse optimization nor a test may silently toggle it. Existing current-mode behavior must be reported honestly if that mode remains selected.

## Selected architecture

### Correctness, tasks 1-3

Task 1 merges href/include identity and nested children independently, then emits both parent metadata and child structure. Match existing parents by href/include identity rather than sibling index. Apply existing scope restrictions recursively. Preserve existing EN labels unless explicitly selected for label translation, maintain allowed EN metadata, and never flatten caching into a top-level workaround.

Task 2 computes navigation warning severity before the no-op return. Clean unchanged navigation remains target_text=None and ok. A blocking unchanged result remains blocked without forcing a file write.

Task 3 performs deterministic preflight before the first paid analyze/translate call. Use the same scope, merge and final-root algorithms with inert labels and frozen R/B readers. Validate required source availability, planned navigation topology and provable dependency gaps. Pending source-shaped Markdown is a preflight simulation only; final generated output still undergoes all normal checks. Conditional/unsupported cases must produce a named deferred check or a blocker, never a fabricated success. Existing budget warnings remain warnings unless an actual mandatory obligation is demonstrably unsatisfied.

### Checkpoints, tasks 4-5

Task 4 writes validated completed units as immutable objects through TranscriptStore and persists assembled files, assets, navigation and blockers before a withheld exit. Unit data is written before its completion receipt. File and candidate manifests are separate from unit completion, so one failed batch cannot discard earlier batches. Read back hashes before claiming durability, including with the null store. Retention does not change publication policy or resume eligibility.

Task 5 loads only exact matching validated units from the explicitly selected parent run or last eligible run for this source PR. Candidate-level reuse requires matching H0/H/B/R and all translation fingerprints. Unit receipts remain subject to fresh validation. A navigation-only fix may reuse translations when its translation fingerprint is unchanged. Changed B starts a new plan, renews authority and leases, and cannot inherit candidate approval. Cross-B candidate reuse is not implemented; later unit reuse requires independent identity validation in the newly planned job.

Identity includes repository and source PR; full H0/H/B/R; source/target path and locale; source bytes; structural role and explicit anchors; ordered protected atom types and values; parent context; tokenizer/protection/renderer schema; prompt version and content hash; glossary hash; selected model and nonsecret options. Transport request IDs and credentials are not identity inputs. Raw source recovery and invalid translations can be retained diagnostically but never marked reusable. A store failure is reported as retention failure and must not yield a false success receipt.

### Provenance and differential translation, tasks 6-7

Task 6 introduces explicit coverage units and plans, with no execution change. A unit is reuse_verified, translate_required, materialize_protected or unresolved. Reuse requires exact receipt/source/target identity or a conservative uniquely anchored edit whose surrounding EN bytes remain untouched. Equal counts, kinds, heading text, age, small magnitude and fuzzy LCS are insufficient proof. Repeated or conflicting anchors, changed protected atoms and modified EN invalidate reuse. Unknown units fall back to the smallest safe source boundary, or full translation with an explicit reason.

Glossary's missing fragment obligation selects the full user-token section and unique insertion boundary. It must translate the actual definition, not add an empty anchor or synchronize all historical glossary drift. Auth pages include required PR 50704 debt when EN lineage predates it, together with PR 51079 changes. Protected Mermaid files remain deterministic copies under existing rules; translating their Russian display labels is outside this design.

Task 7 executes only pending prose units, assembles them with proven EN spans and protected source structure, and runs complete validation even when every unit was reused. Reuse is a materialized result, not skip. Protection IDs are remapped through canonical atoms; original destinations, include identities, anchors, code and configuration remain governed by existing contracts. Reused EN spans remain byte-identical except explicitly authorized structural repairs, which must be attributed. Verification checks the complete candidate for link/include/orphan/unsafe issues and checks semantic coverage against the required source projection, while separately preserving existing out-of-obligation EN blocks. This prevents whole-source comparison from silently expanding a required glossary section into an unrelated full-file synchronization.

Coverage plans are portable evidence, not transient inline state. Their canonical manifest is retained in the trusted transcript store and its digest/run ID is bound to the existing authority envelope and exact candidate file hashes. An independent doc_verify invocation validates this binding before using the required-source projection. Missing or tampered evidence fails closed for a units-mode candidate. A repair push binds fresh evidence to K2. Old full-mode envelopes remain readable and do not acquire implied reuse authority.

The existing full-mode tests remain as controls. Update REQUIREMENTS §5/§13 explicitly when enabling the new proven-reuse contract; do not simply resurrect the historical differential heuristic or toggle its ignored flag.

## Rejected alternatives

Publishing incomplete output as draft RED would expose a branch but would weaken completeness policy; draft status is not a merge lock and does not retain partial batches. Retained artifacts provide recovery without that policy change. Existing narrowly typed PUBLISH_RED behavior remains unchanged.

Removing four dependency files would break required links and includes. The assets already make zero calls and glossary still requires its missing definition. Increasing concurrency or output limits does not repair topology or prove source coverage, so model tuning is excluded.

Preflight alone cannot validate generated text or retain successful calls. Checkpoints alone cannot detect bad topology before the first run. The selected approach combines both and adds conservative differential translation after correctness and retention are independently accepted.

## Validation and rollout

Each task has RED/GREEN commands, controls and a tester gate in the implementation plan. Tests use fake LLM clients, in-memory stores and frozen local Git fixtures. Task 1 must compose actual navigation merge with R-GL-16; helper-only fixtures are insufficient. Task 5 must recreate runtime objects between save and load. Task 7 must exercise planning, execution, assembly and independent verification, including preservation of newer EN and attribution of later RU drift.

Final local gate runs the seven new contract suites and the existing unit suite. Baseline failures are reported separately with reproduction evidence; they are not called passing or silently deselected. Controller decides the disposition of unrelated failures before release.

After all gates pass, the controller records tested action SHA, selected authority and frozen B, deploys through the authorized release workflow, and runs PR 51079 once. Success means an actual translation PR, correct nested navigation, no caching/include orphans, retained receipts, independent content review and green doc_verify plus docs build on the same final SHA. Six translated files, a draft PR or local unit tests alone do not establish production success.

## Self-review

Coverage: correctness maps to tasks 1-3, durability and reuse to 4-5, authority-aware planning and execution to 6-7. The plan defines every new cross-task type and method before consumption. No new external service, label, global exemption, source-authority policy or Mermaid translation feature is introduced. Dependencies permit independent design work on tasks 2/4/6, but execution remains strictly sequential as approved.
