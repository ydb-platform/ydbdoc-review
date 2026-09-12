# PR 51079 Translation Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish complete PR 51079 translations reliably while retaining paid output and translating only source units whose coverage cannot be proven.

**Architecture:** Repair mixed navigation nodes and add deterministic preflight without weakening final gates. Persist immutable validated translation checkpoints through the existing transcript store, then add exact-match resume and a separate provenance-aware coverage planner. Execute only pending units while preserving proven EN spans and validating the entire assembled candidate.

**Tech Stack:** Python >=3.11, dataclasses, hashlib/json, pytest, existing Markdown/YFM parser and protection/renderer pipeline, PyYAML, TranscriptStore (YDB/S3/in-memory implementations).

**Spec:** [2026-09-07-pr51079-translation-recovery-design.md](../specs/2026-09-07-pr51079-translation-recovery-design.md)

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

---

## Execution rules and file responsibilities

The user already selected controller-dispatched sequential development. Do not ask for another execution-mode choice. The header's recommended subagent workflow is operated by the controller; a developer uses executing-plans on only its assigned task. Developer implementation may use an isolated worktree under the using-git-worktrees skill. Do not dispatch tasks in parallel even where dependencies are independent.

Existing `navigation/toc.py` owns tree parse/merge/serialization. Existing `pipeline/navigation_merge.py` owns navigation result classification. New `pipeline/translation_preflight.py` owns model-free planning checks. New `ops/translation_checkpoint.py` owns receipt schemas, durable immutable objects and exact loading; it does not own publication. New `translation/coverage.py` owns source-unit obligations and byte-span assembly. Existing harness context/state classes carry optional checkpoint and coverage inputs; default None preserves full-mode behavior. Existing workflow freezes authority, invokes these stages and keeps final gates/publication ownership.

Checkpoint identity and coverage types below are public cross-task interfaces. Implement their JSON codecs and validation in the defining task. Later tasks must use these exact names. Internal helpers can be added only within assigned files. Tests may define local helpers, but no global conftest or unrelated test changes.

All commands run at repository root with `.venv/bin/python`. If an isolated checkout lacks `.venv`, point its execution environment at the existing interpreter before running these commands; do not install new dependencies or edit project settings for this plan. Each RED must be a behavior failure, not a syntax/import mistake: for a new module, first add its typed interface with a conservative nonfunctional result, then demonstrate the stated assertion failure.

## Correctness tasks 1-3

### Task 1: Preserve mixed navigation nodes and prove composed reachability

**Files:**

- Modify: `src/ydbdoc_review/navigation/toc.py::_merge_toc_tree_nodes`, `_serialize_toc_node`.
- Test: `tests/unit/test_navigation_toc.py`.
- Create: `tests/unit/test_pr51079_navigation_contract.py`.

**Interfaces:**

- Consumes existing `TocNode`, `merge_en_toc_yaml`, `merge_navigation_pair`, `apply_orphan_toc_page_checks`.
- Produces unchanged `merge_en_toc_yaml(en_main_yaml: str, ru_pr_yaml: str, *, translate_hrefs: set[str], translate_name: Callable[[str], str], ru_base_hrefs: set[str] | None = None, translate_include_paths: set[str] | None = None, ru_base_include_paths: set[str] | None = None, restrict_gap_fill_to_scope: bool = False, keep_en_hrefs: set[str] | frozenset[str] | None = None) -> str`, now retaining mixed href/children semantics.

- [ ] **Step 1: Add the minimal behavior regression.**

```python
import pytest
import yaml
from ydbdoc_review.navigation.toc import merge_en_toc_yaml

@pytest.mark.parametrize("parent_scoped", [False, True])
def test_scoped_caching_child_survives_href_parent(parent_scoped):
    ru = "items:\n- name: Auth RU\n  href: authentication.md\n  items:\n  - name: Cache RU\n    href: caching-authentication-results.md\n"
    en = "items:\n- name: Authentication\n  href: authentication.md\n"
    scope = {"caching-authentication-results.md"}
    if parent_scoped:
        scope.add("authentication.md")
    merged = merge_en_toc_yaml(
        en, ru, translate_hrefs=scope,
        translate_name=lambda name: {"Auth RU": "Authentication", "Cache RU": "Caching"}[name],
        restrict_gap_fill_to_scope=True,
    )
    parent = yaml.safe_load(merged)["items"][0]
    assert parent["href"] == "authentication.md"
    assert parent["name"] == "Authentication"
    assert parent["items"][0]["href"] == "caching-authentication-results.md"
```

- [ ] **Step 2: Record RED.** Run `.venv/bin/python -m pytest tests/unit/test_pr51079_navigation_contract.py -v`. Expected: missing parent `items` assertion/KeyError, proving the child is lost even when scoped.
- [ ] **Step 3: Add serialization and scope controls.** Extend the same file with a parse/serialize mixed-node test, a nested href+include+items node, two reordered siblings with distinct hrefs, child-only scope preserving existing parent label, out-of-scope child exclusion, allowed EN-only sibling retention and explicitly removed child exclusion. Add a real local-Git fixture: root toc includes security sidebar, frozen EN sidebar is flat, six PairRunResults contain auth_config/authentication/glossary/caching/two assets. Call `merge_navigation_pair`, pass its result to `apply_orphan_toc_page_checks`, and assert no three gaps. Remove the caching edge in a control and assert all three gaps return. This fixture uses actual parser/merge/orphan logic, with only menu translation mocked.
- [ ] **Step 4: Implement mixed-node recursion and complete serialization.** Retain the existing leaf/include branch policy but merge children before returning the selected node. Resolve an existing parent by href/include first; positional matching is allowed only for anonymous heading shells. Use dataclasses.replace so metadata is retained without mutating the source tree. Core operation:

```python
merged_children = _merge_toc_tree_nodes(
    existing.children if existing is not None else [], ru_node.children,
    en_by_href=en_by_href, en_by_include=en_by_include,
    translate_hrefs=translate_hrefs,
    translate_include_paths=translate_include_paths,
    translate_name=translate_name, ru_base_hrefs=base_hrefs,
    ru_base_include_paths=base_includes,
    restrict_gap_fill_to_scope=restrict_gap_fill_to_scope,
)
selected = replace(selected, children=merged_children)
```

`existing` is the identity-matched EN node; `selected` is the node admitted by existing href/include scope rules. If a scoped child needs an existing out-of-scope parent, retain that parent's EN identity/label as its container. Serialization uses the selected node's own non-child block, removes its old `items:` shell, emits the correctly indented new `items:` plus children, and keeps href/include/when metadata. Do not rewrite the parser or flatten children.
- [ ] **Step 5: Record GREEN and broader controls.** Run `.venv/bin/python -m pytest tests/unit/test_pr51079_navigation_contract.py tests/unit/test_navigation_toc.py tests/unit/test_navigation_merge_pipeline.py tests/unit/test_toc_pr_regressions.py tests/unit/test_toc_bilingual_extras.py tests/unit/test_toc_targets.py tests/unit/test_immutable_reader_contract.py -v`. Expected: new regressions pass; existing failures, if any, are reproduced against the task base and reported separately.
- [ ] **Step 6: Commit only this task.**

```bash
git add src/ydbdoc_review/navigation/toc.py tests/unit/test_navigation_toc.py tests/unit/test_pr51079_navigation_contract.py
git commit -m "fix: preserve scoped children of navigation href nodes"
```

- [ ] **Step 7: Stop for tester gate.** Tester reruns Step 5 at the commit, inspects scope/metadata preservation, and verifies disabling recursive merge restores the composed three-orphan failure. No production rerun.

### Task 2: Keep blocking severity on navigation no-op

**Files:**

- Modify: `src/ydbdoc_review/pipeline/navigation_merge.py::merge_navigation_pair`.
- Create: `tests/unit/test_navigation_noop_contract.py`.

**Interfaces:**

- Consumes existing `_navigation_verdict(warnings: list[str]) -> FileVerdict` and `NavigationRunResult`.
- Produces the same result type and target_text=None no-op representation; verdict now reflects warnings.

- [ ] **Step 1: Add a real merge-result regression with injected validation evidence.**

```python
from unittest.mock import MagicMock, patch
from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.navigation_merge import merge_navigation_pair
from ydbdoc_review.pipeline.pairs import NavigationPair
from ydbdoc_review.translation.glossary import load_glossary

def test_noop_does_not_erase_blocking_warning():
    text = "items:\n- name: Authentication\n  href: authentication.md\n"
    pair = NavigationPair(ru_path="ydb/docs/ru/core/security/toc_p.yaml", en_path="ydb/docs/en/core/security/toc_p.yaml", ru_changed=True)
    prefix = "ydbdoc_review.pipeline.navigation_merge."
    with patch(prefix + "read_text", return_value=text), patch(prefix + "_read_navigation_baselines", return_value=(text, text)), patch(prefix + "merge_en_toc_yaml", return_value=text), patch(prefix + "validate_navigation_merge_warnings", return_value=["scope_not_applied: missing caching child"]), patch(prefix + "read_text_at_upstream_tip", return_value="page"):
        result = merge_navigation_pair(pair, repo_path="/tmp/noop-fixture", merge_base_with="frozen", client=MagicMock(), glossary=load_glossary(), config=load_config(env={}))
    assert result.target_text is None
    assert result.verdict == "blocked"
```

- [ ] **Step 2: Record RED.** Run `.venv/bin/python -m pytest tests/unit/test_navigation_noop_contract.py -v`. Expected: verdict is ok rather than blocked.
- [ ] **Step 3: Compute verdict before byte equality and use it in both branches.**

```python
verdict = _navigation_verdict(warnings)
if merged == en_main or merged.strip() == en_main.strip():
    return NavigationRunResult(
        ru_path=pair.ru_path, en_path=pair.en_path, kind=kind,
        target_text=None, warnings=warnings, verdict=verdict,
    )
```

Keep the existing no-op log. Add clean-no-op and soft-warning-no-op controls with their canonical severities, and assert no write is forced.
- [ ] **Step 4: Record GREEN.** Run `.venv/bin/python -m pytest tests/unit/test_navigation_noop_contract.py tests/unit/test_navigation_merge_pipeline.py tests/unit/test_completeness.py tests/unit/test_publication_policy.py -v`.
- [ ] **Step 5: Commit.**

```bash
git add src/ydbdoc_review/pipeline/navigation_merge.py tests/unit/test_navigation_noop_contract.py
git commit -m "fix: preserve blocking verdict for navigation no-op"
```

- [ ] **Step 6: Stop for tester gate.** Tester repeats Step 4 and confirms unchanged clean output remains a valid no-op. Internally independent of Task 1, but no early execution or production rerun.

### Task 3: Preflight frozen source and navigation before model work

**Files:**

- Create: `src/ydbdoc_review/pipeline/translation_preflight.py`.
- Modify: `src/ydbdoc_review/github/workflow.py::run_doc_translate`.
- Create: `tests/unit/test_translation_preflight.py`.

**Interfaces:**

- Consumes `TranslationScopePlan`, existing scope-reader callables, `planned_toc_extras_for_pair`, `_resolve_toc_merge_scope`, `merge_en_toc_yaml`, existing TOC/include graph validators.
- Produces `PreflightResult(blockers: tuple[str, ...], deferred_checks: tuple[str, ...])` and `preflight_translation(plan: TranslationScopePlan, *, read_ru: Callable[[str], str | None], read_ru_base: Callable[[str], str | None], read_en_base: Callable[[str], str | None], docs_root: str = "ydb/docs") -> PreflightResult`. Readers are already bound to exact R/H0/B; this function never reads Git/worktree itself and accepts no LLM client.

- [ ] **Step 1: Define the dataclass/interface with an empty conservative result and write the missing-source behavior test.**

```python
from ydbdoc_review.navigation.scope_planner import TranslationScopePlan
from ydbdoc_review.pipeline.translation_preflight import preflight_translation

def test_missing_source_is_a_preflight_blocker():
    path = "ydb/docs/ru/core/security/authentication.md"
    plan = TranslationScopePlan(doc_ru_paths=frozenset({path}), doc_from_diff=frozenset({path}), doc_from_main=frozenset(), nav_ru_paths=frozenset(), nav_from_diff=frozenset(), nav_from_main=frozenset())
    result = preflight_translation(plan, read_ru=lambda path: None, read_ru_base=lambda path: None, read_en_base=lambda path: None)
    assert any("missing_source" in message and path in message for message in result.blockers)
```

- [ ] **Step 2: Record RED.** Run `.venv/bin/python -m pytest tests/unit/test_translation_preflight.py -v`. Expected: no missing_source blocker from initial interface.
- [ ] **Step 3: Check mandatory sources and construct simulated topology.**

```python
blockers = []
for path in sorted(plan.doc_ru_paths - plan.doc_deleted):
    if read_ru(path) is None:
        blockers.append(f"missing_source: {path}")
```

For each planned sidebar use frozen strings, exact planned extras, `_resolve_toc_merge_scope` and the unchanged `merge_en_toc_yaml` interface from Task 1, passing `translate_name=lambda name: name`. Run topology and root validation against B plus this simulation; source-shaped Markdown supplies include structure only. Use existing structural include parsing. Missing required includes, disconnected parent sidebars and a deliberately reintroduced Task 1 loss must produce named blockers. Do not promote unrelated budget warnings into fatal errors; block only a proven unsatisfied mandatory dependency. Record conditional checks that require actual generated output in deferred_checks.
- [ ] **Step 4: Wire workflow before its first analyze or translate call.** Immediately after filtering the complete scope, call preflight with the same frozen readers. On blockers, create the normal incomplete result and follow existing reporting/ops finalization without executing file or label LLM calls. Add a workflow test with `client.chat.side_effect = AssertionError("preflight must run first")`; invalid topology returns WITHHOLD and the call count stays zero. Valid topology reaches the fake translator. A malformed fake translation still fails final gates, demonstrating preflight is not final approval.
- [ ] **Step 5: Record GREEN.** Run `.venv/bin/python -m pytest tests/unit/test_translation_preflight.py tests/unit/test_pr51079_navigation_contract.py tests/unit/test_github_workflow.py tests/unit/test_immutable_reader_contract.py -v`.
- [ ] **Step 6: Commit.**

```bash
git add src/ydbdoc_review/pipeline/translation_preflight.py src/ydbdoc_review/github/workflow.py tests/unit/test_translation_preflight.py
git commit -m "feat: preflight translation dependencies before model calls"
```

- [ ] **Step 7: Stop for tester gate.** Tester confirms zero calls on proven source/topology failures, no fabricated roots, and fresh final checks for valid preflight. Depends on Task 1's topology semantics. No production rerun.

## Checkpoint tasks 4-5

### Task 4: Persist completed translation units and retained candidates

**Files:**

- Create: `src/ydbdoc_review/ops/translation_checkpoint.py`.
- Modify: `src/ydbdoc_review/translation/translator.py::translate_segments`.
- Modify: `src/ydbdoc_review/harness/context.py::HarnessContext`, `src/ydbdoc_review/harness/pr_context.py::PRHarnessContext`, `src/ydbdoc_review/harness/pr_steps.py::ExecutePairPlansStep`, `src/ydbdoc_review/harness/steps.py::TranslateStep` callback wiring only.
- Modify: `src/ydbdoc_review/pipeline/orchestrator.py::run_pr_translation`, `src/ydbdoc_review/github/workflow.py::run_doc_translate`.
- Create: `tests/unit/test_translation_checkpoint.py`.

**Interfaces:**

- Consumes unchanged `TranscriptStore.put/get/list_keys`, `RuAuthority`, existing segment validator and file results.
- Produces frozen `CheckpointIdentity(authority: RuAuthority, translation_fingerprint: str)`; frozen `UnitReceipt(identity: CheckpointIdentity, unit_key: str, source_hash: str, target_hash: str, object_key: str, validated: bool)`; `CheckpointWriter(store: TranscriptStore, run_id: str, identity: CheckpointIdentity)` with `save_unit(unit_key: str, source: bytes, target: bytes, *, validated: bool) -> UnitReceipt`, `save_file(path: str, source: bytes, target: bytes, *, blockers: tuple[str, ...]) -> None`, `save_navigation(path: str, target: bytes | None, *, blockers: tuple[str, ...]) -> None`, `finish(*, status: str, blockers: tuple[str, ...], scope: dict[str, tuple[str, ...]]) -> None`. Scope maps each path to its exact admission reasons. A None navigation target records a baseline no-op, not deletion.
- Produces `translation_unit_key(*, source: bytes, source_path: str, target_locale: str, atom_signature: tuple[tuple[str, str], ...], parent_context: str) -> str` using canonical JSON plus SHA-256. Atom values, order and context are mandatory.
- Adds optional `checkpoint: CheckpointWriter | None = None` through workflow/orchestrator/PRHarnessContext/HarnessContext. Adds optional `on_validated_segment: Callable[[Segment, str], None] | None = None` to translate_segments; callback runs promptly after a completed batch's segments pass existing validation. File/asset saves happen at the PR result boundary, before subsequent navigation can fail.

- [ ] **Step 1: Add schema/interface plus a durable-write test.**

```python
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.config.loader import RuAuthorityMode
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore
from ydbdoc_review.ops.translation_checkpoint import CheckpointIdentity, CheckpointWriter

def test_completed_unit_is_durable_before_candidate_finishes():
    authority = RuAuthority(source_repo="ydb-platform/ydb", source_pr=51079, source_base_sha="1" * 40, source_head_sha="2" * 40, baseline_sha="3" * 40, ru_sha="2" * 40, mode=RuAuthorityMode.SOURCE_PRESERVING)
    store = InMemoryTranscriptStore()
    writer = CheckpointWriter(store, "run-a", CheckpointIdentity(authority, "4" * 64))
    receipt = writer.save_unit("5" * 64, b"source", b"target", validated=True)
    assert store.get("run-a", receipt.object_key) == b"target"
    assert receipt.validated
```

The implementation interface is initially nonpersistent so the test fails on missing bytes, not import errors. `RuAuthorityMode` is defined in the existing config.loader module.
- [ ] **Step 2: Record RED.** Run `.venv/bin/python -m pytest tests/unit/test_translation_checkpoint.py -v`. Expected: completed unit bytes are not retained.
- [ ] **Step 3: Implement immutable object and receipt writes.**

```python
target_hash = hashlib.sha256(target).hexdigest()
object_key = f"translation/v1/objects/{target_hash}"
store.put(run_id, object_key, target)
if store.get(run_id, object_key) != target:
    raise RuntimeError("translation checkpoint write verification failed")
```

Write the canonical receipt only after object read-back succeeds. Store under `translation/v1/units/{unit_key}.json`. Reject a second different receipt under the same key. Diagnostic invalid output uses `validated=False` and is never announced reusable. Candidate `translation/v1/manifest.json` records authority, full nonsecret fingerprints, unit receipt hashes, files, nav outputs, source/target hashes, scope reasons and stage; write it last and read it back before recording retained success. A partial run can have valid unit receipts without a final manifest.
- [ ] **Step 4: Invoke save callbacks as batches complete.** In the futures completion loop, validate returned segment translations and persist before waiting for all remaining futures. Preserve returned segment order for assembly. Do not persist recovery source as a valid translation. Capture assembled files and protected assets, then capture nav/blockers before WITHHOLD. Add tests: two successful batches plus a failing third retain the first two; withheld workflow makes no commit/push calls; store failure gives explicit retention error; null store cannot fake durability; diagnostic soft-keep is not reusable; all-protected asset still saves without LLM.
- [ ] **Step 5: Record GREEN.** Run `.venv/bin/python -m pytest tests/unit/test_translation_checkpoint.py tests/unit/test_translator.py tests/unit/test_harness_pr.py tests/unit/test_github_workflow.py tests/unit/test_publication_policy.py -v`.
- [ ] **Step 6: Commit.**

```bash
git add src/ydbdoc_review/ops/translation_checkpoint.py src/ydbdoc_review/translation/translator.py src/ydbdoc_review/harness/context.py src/ydbdoc_review/harness/pr_context.py src/ydbdoc_review/harness/pr_steps.py src/ydbdoc_review/harness/steps.py src/ydbdoc_review/pipeline/orchestrator.py src/ydbdoc_review/github/workflow.py tests/unit/test_translation_checkpoint.py
git commit -m "feat: retain validated translation checkpoints before publication"
```

- [ ] **Step 7: Stop for tester gate.** Tester injects partial/store failures and verifies artifact hashes and unchanged publication behavior. Retention only: do not activate resume in this task. No production rerun.

### Task 5: Resume exact validated units with fresh final gates

**Files:**

- Modify: `src/ydbdoc_review/ops/translation_checkpoint.py`.
- Modify: `src/ydbdoc_review/translation/translator.py::translate_segments`, `src/ydbdoc_review/harness/context.py`, `src/ydbdoc_review/harness/pr_context.py`, `src/ydbdoc_review/harness/pr_steps.py`, `src/ydbdoc_review/harness/steps.py::TranslateStep` resume callback wiring only, `src/ydbdoc_review/pipeline/orchestrator.py`.
- Modify: `src/ydbdoc_review/github/workflow.py::run_doc_translate`, `src/ydbdoc_review/ops/job_state.py`.
- Create: `tests/unit/test_translation_resume.py`.

**Interfaces:**

- Consumes Task 4 identity, receipt and durable object format.
- Produces frozen `VerifiedUnit(receipt: UnitReceipt, source: bytes, target: bytes)` and `load_verified_unit(store: TranscriptStore, parent_run_id: str, identity: CheckpointIdentity, unit_key: str, source: bytes) -> VerifiedUnit | None`.
- Adds optional `resume_parent_run_id: str | None = None` to checkpoint runtime context; use the existing explicitly selected ops parent run, otherwise the last eligible run found through the source PR ledger. No new GitHub label or cross-PR discovery. Existing continue ACL/eligibility stays enforced.
- Adds optional `load_validated_segment: Callable[[Segment], str | None] | None = None` to translate_segments. TranslateStep supplies this callback from the checkpoint context; the translator revalidates the returned text before accepting it. Task 4's completion callback remains separate.

- [ ] **Step 1: Write exact-reload and identity-drift controls.**

```python
from dataclasses import replace
import pytest
from ydbdoc_review.config.loader import RuAuthorityMode
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore
from ydbdoc_review.ops.translation_checkpoint import CheckpointIdentity, CheckpointWriter, load_verified_unit

@pytest.fixture
def saved_checkpoint():
    authority = RuAuthority(source_repo="ydb-platform/ydb", source_pr=51079, source_base_sha="1" * 40, source_head_sha="2" * 40, baseline_sha="3" * 40, ru_sha="2" * 40, mode=RuAuthorityMode.SOURCE_PRESERVING)
    store = InMemoryTranscriptStore()
    writer = CheckpointWriter(store, "run-a", CheckpointIdentity(authority, "4" * 64))
    receipt = writer.save_unit("5" * 64, b"source", b"target", validated=True)
    del writer
    return store, receipt, b"source"

def test_changed_translation_fingerprint_never_reuses(saved_checkpoint):
    store, receipt, source = saved_checkpoint
    changed = replace(receipt.identity, translation_fingerprint="f" * 64)
    assert load_verified_unit(store, "run-a", changed, receipt.unit_key, source) is None

def test_recreated_loader_reads_exact_completed_bytes(saved_checkpoint):
    store, receipt, source = saved_checkpoint
    loaded = load_verified_unit(store, "run-a", receipt.identity, receipt.unit_key, source)
    assert loaded is not None
    assert loaded.target == b"target"
```

Add a disk-backed test store local to this test module implementing the existing four TranscriptStore methods and run a separate Python process to reload canonical receipt bytes. Its put/get methods encode run_id/object_key below pytest tmp_path, list_keys enumerates those files, and exists_run tests that run directory; no external store is contacted.
- [ ] **Step 2: Record RED.** Run `.venv/bin/python -m pytest tests/unit/test_translation_resume.py -v`. Initial loader returns None; the exact completed-bytes assertion fails.
- [ ] **Step 3: Implement fail-closed loading.**

```python
if receipt.identity != identity or not receipt.validated:
    return None
if hashlib.sha256(source).hexdigest() != receipt.source_hash:
    return None
target = store.get(parent_run_id, receipt.object_key)
if target is None or hashlib.sha256(target).hexdigest() != receipt.target_hash:
    return None
return VerifiedUnit(receipt=receipt, source=source, target=target)
```

Decode errors, unsupported schema, missing completion receipt or object hashes return a cache miss plus explicit diagnostic. Before consuming loaded target in translate_segments, call existing `validate_segment_translation` with the current Segment. Invalid target falls back to translation and remains diagnostic, never green reuse. The old text/path/lang dict key is not a durable identity.
- [ ] **Step 4: Integrate same-input resume.** Use loaded valid entries before dispatching batches; translate only missing entries. Same translation fingerprint with changed navigation code can reuse units while recomputing all nav. Changed B, R or mode fails identity and starts fresh planning; never reuse branch leases or final verdict. Add fake-LLM workflow tests for zero repeated calls, partial-only calls, changed glossary/prompt/model/protection/context/atom invalidation, corrupted objects, changed source, and branch drift still rejecting publication. Do not bypass `doc_continue` admission using a receipt.
- [ ] **Step 5: Record GREEN.** Run `.venv/bin/python -m pytest tests/unit/test_translation_resume.py tests/unit/test_translation_checkpoint.py tests/unit/test_translator.py tests/unit/test_immutable_reader_contract.py tests/unit/test_a05_authority_provenance_contract.py tests/unit/test_github_workflow.py -v`.
- [ ] **Step 6: Commit.**

```bash
git add src/ydbdoc_review/ops/translation_checkpoint.py src/ydbdoc_review/translation/translator.py src/ydbdoc_review/harness/context.py src/ydbdoc_review/harness/pr_context.py src/ydbdoc_review/harness/pr_steps.py src/ydbdoc_review/harness/steps.py src/ydbdoc_review/pipeline/orchestrator.py src/ydbdoc_review/github/workflow.py src/ydbdoc_review/ops/job_state.py tests/unit/test_translation_resume.py
git commit -m "feat: resume matching validated translation units"
```

- [ ] **Step 7: Stop for tester gate.** Tester recreates process state, checks identities, and confirms all final gates execute despite a zero-call retry. Depends on Task 4. No production rerun.

## Provenance and differential tasks 6-7

### Task 6: Define conservative source coverage plans

**Files:**

- Create: `src/ydbdoc_review/translation/coverage.py`.
- Modify: `src/ydbdoc_review/pipeline/analyze.py::PairContent`, `src/ydbdoc_review/github/provenance.py` coverage evidence validation only.
- Modify: `REQUIREMENTS_RU.md` §5/§13, `src/ydbdoc_review/config/default.yaml` policy comments only.
- Create: `tests/unit/test_translation_coverage.py`.

**Interfaces:**

- Consumes `RuAuthority`, Task 5 `VerifiedUnit`, existing Markdown/YFM parser and canonical atom extraction. No heuristic translation-age API is used.
- Produces frozen `CoverageUnit(key: str, action: Literal["reuse_verified", "translate_required", "materialize_protected", "unresolved"], source: str, en_span: tuple[int, int] | None, target: str | None, reason: str)` and frozen `CoveragePlan(source_path: str, source_hash: str, en_hash: str | None, units: tuple[CoverageUnit, ...], required_fragments: frozenset[str], mode: Literal["full", "units"])`.
- Produces `plan_source_coverage(*, source_path: str, source_text: str, existing_en: str | None, authority: RuAuthority, required_fragments: frozenset[str] = frozenset(), verified_units: tuple[VerifiedUnit, ...] = ()) -> CoveragePlan`. `en_span` uses Python string offsets consistently, while hashes encode UTF-8. A zero-length span means insertion at a proven boundary. A unit with target None requires translation or resolution; protected source material has deterministic target.
- Adds optional `coverage_plan: CoveragePlan | None = None` to PairContent. Required-fragment evidence must come from the scope planner's actual exact-fragment dependency reason, not any arbitrary page link. No plan is executed in this task.
- Produces canonical JSON codecs `encode_coverage_plan(plan: CoveragePlan) -> bytes` and `decode_coverage_plan(data: bytes) -> CoveragePlan`; decoder rejects unknown schemas, invalid action values, overlapping spans and missing hashes. These portable plans are required for later independent verification, not just inline state.

- [ ] **Step 1: Add planner interface and stale-EN behavior test.**

```python
import pytest
from ydbdoc_review.config.loader import RuAuthorityMode
from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.translation.coverage import plan_source_coverage

@pytest.fixture
def current_authority():
    return RuAuthority(source_repo="ydb-platform/ydb", source_pr=51079, source_base_sha="1" * 40, source_head_sha="2" * 40, baseline_sha="3" * 40, ru_sha="3" * 40, mode=RuAuthorityMode.CURRENT)

def test_empty_current_ru_diff_does_not_prove_en_coverage(current_authority):
    result = plan_source_coverage(
        source_path="ydb/docs/ru/core/security/authentication.md",
        source_text="# Authentication\n\nNew provider requirements.\n",
        existing_en="# Authentication\n\nOld provider requirements.\n",
        authority=current_authority,
    )
    assert any(unit.action == "translate_required" for unit in result.units)
    assert all(unit.action != "reuse_verified" for unit in result.units)
```

Initial empty plan fails the required-unit assertion; this test explicitly models B=R and stale EN.
- [ ] **Step 2: Record RED.** Run `.venv/bin/python -m pytest tests/unit/test_translation_coverage.py -v`.
- [ ] **Step 3: Implement proof-based classification.**

```python
if verified_target is not None:
    action, target, reason = "reuse_verified", verified_target, "matching validated source and EN receipt"
elif protected_only:
    action, target, reason = "materialize_protected", source_unit, "protected source structure"
else:
    action, target, reason = "translate_required", None, "no matching source coverage evidence"
```

Here `verified_target` is accepted only after receipt authority/fingerprint/source bytes and current EN span hash match; `protected_only` comes from existing protected-atom extraction; `source_unit` is the parser's exact source slice. Unit keys use Task 4 canonical identity. No same-count/kind/age/magnitude/LCS proof. Without a unique source-to-EN boundary use mode full and state the fallback reason.
- [ ] **Step 4: Add required-fragment insertion planning and provenance controls.** In glossary locate the complete section beginning at unique explicit `user-token` and ending at the next same-or-higher heading; prove the surrounding existing EN insertion context using unique stable anchors. Plan only that section's insertion and record remaining EN as untouched spans. Ambiguous duplicate anchors reject units mode. Add tests covering no EN caching, protected assets, H0 #50704 debt absent in EN, newer EN differing from a receipt, changed href/include/atom value, shifted IDs, repeated headings, and later #52355 attribution without source-scope expansion. Preserve authority selection and expose fallback reasons and counts. Update requirement prose to define the future proven-reuse contract; execution remains full until Task 7.
- [ ] **Step 5: Record GREEN.** Run `.venv/bin/python -m pytest tests/unit/test_translation_coverage.py tests/unit/test_translation_resume.py tests/unit/test_a05_authority_provenance_contract.py tests/unit/test_source_preserving_label_contract.py tests/unit/test_differential_translation.py tests/unit/test_differential_partial_seed.py -v`.
- [ ] **Step 6: Commit.**

```bash
git add src/ydbdoc_review/translation/coverage.py src/ydbdoc_review/pipeline/analyze.py src/ydbdoc_review/github/provenance.py REQUIREMENTS_RU.md src/ydbdoc_review/config/default.yaml tests/unit/test_translation_coverage.py
git commit -m "feat: plan translation units from proven source coverage"
```

- [ ] **Step 7: Stop for tester gate.** Tester checks all required source obligations are represented, rejects any invented synchronization authority, and verifies no execution change. No guaranteed saving is asserted for unproven historical EN lineage. No production rerun.

### Task 7: Execute proven units and validate the complete candidate

**Files:**

- Modify: `src/ydbdoc_review/translation/coverage.py`.
- Modify: `src/ydbdoc_review/harness/steps.py::TranslateStep`, `_render_translated_from_source`, `src/ydbdoc_review/harness/state.py::FileRunState`, `src/ydbdoc_review/harness/pair.py::run_pair_plan`.
- Modify: `src/ydbdoc_review/github/workflow.py::run_doc_translate`, `run_doc_verify`; `src/ydbdoc_review/reporting/builder.py` coverage summary.
- Modify: `src/ydbdoc_review/github/provenance.py` to bind the coverage manifest digest to the candidate using the existing authority evidence envelope.
- Modify: `src/ydbdoc_review/navigation/scope_planner.py` dependency-reason metadata only, `src/ydbdoc_review/pipeline/analyze.py::PairContent` plan plumbing.
- Modify: `tests/unit/test_protect_translate_matrix.py`, `tests/unit/test_pair_no_old_en_shortcuts.py`.
- Create: `tests/unit/test_source_preserving_translation.py`.

**Interfaces:**

- Consumes Task 6 CoveragePlan and existing full translation/protection/validation functions; consumes Task 5 resume before new model dispatch.
- Produces `assemble_coverage(plan: CoveragePlan, *, existing_en: str | None, translated_units: dict[str, str]) -> str`. It rejects changed input hashes, missing unit keys, overlapping/reversed/out-of-bounds spans and unresolved units with ValueError. Adds optional `coverage_plan: CoveragePlan | None = None` to FileRunState. None keeps the explicit full path.
- Full-mode validation remains unchanged. Units-mode semantic comparison uses the plan's required source units; final candidate-tree/structural/unsafe checks always inspect complete output. Reporting records units reused/translated/protected and each fallback reason, not a fabricated full translation count.
- Produces a versioned coverage evidence object in TranscriptStore under `translation/v1/coverage/{candidate_sha}.json`, containing exact authority, encoded plans, baseline EN hashes, final candidate file hashes and a canonical SHA-256 digest. Extend the existing authority wire envelope with optional coverage version/run ID/digest, keeping strict old-envelope decoding compatible. `run_doc_verify` loads only this bound trusted-store object and checks all hashes against the frozen authority and candidate; missing/corrupt/mismatched evidence fails closed for units-mode candidates. Repairs bind a new manifest to K2 before further verification; they never inherit a stale candidate digest. No new PR label is introduced.

- [ ] **Step 1: Add assembly preservation regression.**

```python
import hashlib
from ydbdoc_review.translation.coverage import CoveragePlan, CoverageUnit, assemble_coverage

def test_insertion_preserves_all_unaffected_en_bytes():
    en = "# Existing\n\nCorrect accepted prose.\n"
    unit = CoverageUnit(key="new", action="translate_required", source="## New\n\nRequired source.\n", en_span=(len(en), len(en)), target=None, reason="required missing section")
    plan = CoveragePlan(source_path="ydb/docs/ru/core/concepts/glossary.md", source_hash=hashlib.sha256(unit.source.encode()).hexdigest(), en_hash=hashlib.sha256(en.encode()).hexdigest(), units=(unit,), required_fragments=frozenset({"user-token"}), mode="units")
    addition = "\n## User token {#user-token}\n\nTranslated definition.\n"
    result = assemble_coverage(plan, existing_en=en, translated_units={"new": addition})
    assert result == en + addition
```

- [ ] **Step 2: Record RED.** Run `.venv/bin/python -m pytest tests/unit/test_source_preserving_translation.py -v`. Initial conservative assembly rejects units mode; expected assertion/explicit not-enabled failure, not an import mistake.
- [ ] **Step 3: Implement validated span assembly.**

```python
pieces = []
cursor = 0
for start, end, replacement in sorted(edits, key=lambda item: (item[0], item[1])):
    if start < cursor or end < start or end > len(existing_en):
        raise ValueError("invalid or overlapping coverage span")
    pieces.extend((existing_en[cursor:start], replacement))
    cursor = end
pieces.append(existing_en[cursor:])
assembled = "".join(pieces)
```

Build edits only after matching en_hash and resolving every plan unit. Reject ambiguous multiple insertions at one offset unless order was explicitly established by source units. Full/new-file mode renders authoritative source with complete translations through existing renderer. For each new unit parse/protect using existing machinery, translate pending prose, remap atoms canonically, and restore protected bytes. Validate actual source_hash at the workflow/state boundary before calling assembly.
- [ ] **Step 4: Wire coverage into translate and verify.** Pass actual source-API vs fragment-dependency reasons from scope planning to PairContent; build plans using frozen readers and valid receipts. Dispatch only translate_required units. Keep each unit's translation on the normal checkpoint path. No semantic-noop or skip return is introduced. Set the assembled full result then run existing structural repair and final document/tree gates. Record any authorized structural repair outside a translated span. Persist and bind coverage evidence using the interface above. Verify required source coverage without treating unrelated retained glossary text as newly authorized translation scope, and retain complete-tree safety validation. Add mocked-call tests for exactly pending units, zero-call all-reuse still validated, new caching fully translated and assets zero-call; a fresh independent verifier must load evidence successfully, while missing/different/tampered evidence and stale K-after-K2 evidence must block.
- [ ] **Step 5: Replace conflicting old expectations deliberately and add negative controls.** Keep a full-mode test asserting no heuristic seeding. Units-mode tests prove same-text/different-href, changed includes, anchors, config blocks, shifted placeholders and current EN hash drift cannot pass bad reuse. Source deletion needs a uniquely proven mapped span or conservative fallback. Corrupt reused output must trigger existing unsafe blockers. The composed frozen PR51079 fixture traverses plan, fake translation, nav, apply and independent verification: six docs/one nav, full user-token definition exactly once, unaffected glossary EN byte-identical, accepted #52330 repairs preserved and #52355 later drift separately reported.
- [ ] **Step 6: Record GREEN.** Run `.venv/bin/python -m pytest tests/unit/test_source_preserving_translation.py tests/unit/test_translation_coverage.py tests/unit/test_translation_resume.py tests/unit/test_pr51079_navigation_contract.py tests/unit/test_protect_translate_matrix.py tests/unit/test_pair_no_old_en_shortcuts.py tests/unit/test_harness_pair_toc_reachable.py tests/unit/test_href_parity.py tests/unit/test_toc_targets.py tests/unit/test_immutable_reader_contract.py -v`.
- [ ] **Step 7: Commit.**

```bash
git add src/ydbdoc_review/translation/coverage.py src/ydbdoc_review/harness/steps.py src/ydbdoc_review/harness/state.py src/ydbdoc_review/harness/pair.py src/ydbdoc_review/github/workflow.py src/ydbdoc_review/github/provenance.py src/ydbdoc_review/reporting/builder.py src/ydbdoc_review/navigation/scope_planner.py src/ydbdoc_review/pipeline/analyze.py tests/unit/test_protect_translate_matrix.py tests/unit/test_pair_no_old_en_shortcuts.py tests/unit/test_source_preserving_translation.py
git commit -m "feat: execute proven translation units with full candidate validation"
```

- [ ] **Step 8: Stop for tester gate and aggregate acceptance.** Tester repeats Step 6, then runs `.venv/bin/python -m pytest tests/unit -v` and `git diff --check HEAD~1 HEAD`. Report baseline failures with exact reproduction and affected assertions; do not report a green full suite if it is not green. Controller resolves any unrelated baseline disposition before release. No developer production rerun.

## Final controller handoff and dependency order

Tasks 1-3 are correctness; 4-5 are retention/recovery; 6-7 are provenance/differential optimization. Task 1 precedes 3; 4 precedes 5; 6 precedes 7; 7 uses 5. Tasks 2, 4 and the design of 6 can be reasoned about independently, but the approved execution order remains 1→2→3→4→5→6→7 with independent tester GO after each commit.

After every gate passes the controller pins tested action SHA, H0/H/B/R/mode, releases through the authorized workflow, and runs PR 51079 once. Controller verifies actual translation PR creation, caching nested under authentication, no three orphan gaps, complete checkpoint receipts, independent content review, doc_verify and docs build green on the same final candidate SHA. Do not change source-authority labels as an optimization side effect. Do not add a paid rerun just to demonstrate resume; local exact-input tests establish that property.

## Plan self-review

Spec coverage: mixed-node loss and R-GL-16 composition are Task 1; no-op classification Task 2; early deterministic gate Task 3; durable units/manifests Task 4; process-independent reuse and invalidation Task 5; H0/H/B/R vs EN lineage and later-RU separation Task 6; assembly, reporting and full candidate validation Task 7. Out-of-scope content and all release constraints are copied verbatim above.

Type consistency: CheckpointIdentity, UnitReceipt and CheckpointWriter are defined in Task 4; VerifiedUnit/loading in Task 5; CoverageUnit/Plan in Task 6; assembly in Task 7. Offsets are strings and hashes are UTF-8 bytes throughout. Existing wrappers carry optional inputs with defaults. No task consumes an undefined new interface. Test commands name explicit files, commit commands stage explicit paths, and all seven tasks end at tester gates.
