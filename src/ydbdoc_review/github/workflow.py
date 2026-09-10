"""GitHub Actions workflow: doc_translate and doc_verify."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import NoReturn
from urllib.parse import unquote

from ydbdoc_review.config.loader import Config, RuAuthorityMode, load_config
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.github.errors import GitHubAPIError, GitHubConfigError
from ydbdoc_review.github.git_ops import (
    RefMutationReceipt,
    RefMutationStatus,
    RemoteRefLease,
    commit_changes_between,
    commit_parent_sha,
    delete_remote_branch_with_lease,
    ensure_commit,  # noqa: F401 - compatibility seam for workflow tests
    first_parent_commit_changes,
    git_commit_paths,
    git_head_sha,
    prepare_translation_branch_on_base,
    push_branch,
    read_text,
    read_text_at_commit,
    resolve_commit_ref,
    rollback_pushed_branch,
    write_text,
)
from ydbdoc_review.github.pr import (
    PullRequestContext,
    build_pairs_from_changes,
    is_fork_head,
    is_translation_pr_branch,
    is_verify_fixup_branch,
    list_pr_file_changes_api,
    list_pr_file_changes_git,
    load_pair_contents,
    load_verify_navigation_ru_texts,
    load_verify_pair_contents,
    merge_pr_file_changes,
    parse_repo,
    parse_source_pr_from_text,
    pull_request_context,
    repo_https_clone_url,
    source_pr_number_from_branch,
    translate_ru_content_ref,  # noqa: F401 - A05 mutation-injection seam
    translation_branch_base,
    translation_pr_base,
    verify_fixup_branch,
    verify_fixup_pr_base,
)
from ydbdoc_review.github.provenance import (
    RuAuthority,
    TranslationArtifactProvenance,
    bind_translation_artifact,
    freeze_ru_authority,
    parse_authority_evidence,
    validate_authority_evidence,
)
from ydbdoc_review.harness.pr_context import PRHarnessContext
from ydbdoc_review.harness.pr_profiles import VERIFY_PR_PROFILE
from ydbdoc_review.harness.pr_runner import PRHarness
from ydbdoc_review.harness.pr_state import PRRunState
from ydbdoc_review.llm.client import YandexLLMClient, create_llm_client
from ydbdoc_review.navigation.dependency_budget import MarkdownDependencyBudget
from ydbdoc_review.navigation.redirects import (
    follow_redirect_repo_md_path,
    redirect_source_repo_md_paths,
)
from ydbdoc_review.navigation.scope_planner import (
    TranslationScopePlan,
    doc_pairs_from_plan,
    make_repo_scope_readers,
    merge_navigation_pair_lists,
    navigation_pairs_from_plan,
    plan_translation_scope,
    synthetic_changes_from_plan,
)
from ydbdoc_review.ops.continue_cmd import find_latest_continue_instruction
from ydbdoc_review.ops.coverage_rebind import load_attested_coverage_evidence
from ydbdoc_review.ops.feedback_ctx import continue_feedback_scope
from ydbdoc_review.ops.job_state import (
    CONTINUABILITY_STORE_KEY,
    ContinuabilityState,
    clear_continuability,
    dump_continuability_json,
    load_continuability,
    load_continuability_from_bytes,
    mark_continuable,
    relative_state_path,
)
from ydbdoc_review.ops.lifecycle import (
    OpsContext,
    append_retention_footer,
    begin_ops_job,
    compose_continue_feedback,
    finish_ops_job,
    load_parent_run_context,
)
from ydbdoc_review.ops.transcripts import NullTranscriptStore, TranscriptStore
from ydbdoc_review.ops.translation_checkpoint import (
    CheckpointIdentity,
    CheckpointWriter,
    TranslationCheckpointError,
    load_verified_unit,
    run_has_usable_verified_units,
    translation_unit_key_for_segment,
)
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.analyze import (
    BILINGUAL_SKIP_SUMMARY,
    PairContent,
    PairPlan,
)
from ydbdoc_review.pipeline.completeness import (
    bilingual_en_mirrors,
    completeness_gaps,
    href_only_source_noop_satisfied,
    translation_pr_scope_gaps,
)
from ydbdoc_review.pipeline.navigation_merge import (
    extra_toc_hrefs_from_md_targets,
    run_navigation_merges,
    run_navigation_verifies,
)
from ydbdoc_review.pipeline.orchestrator import run_pr_translation
from ydbdoc_review.pipeline.pairs import (
    DocPair,
    build_navigation_pairs,
    build_verify_navigation_pairs,
    counterpart,
    filter_translation_pr_verify_scope,
)
from ydbdoc_review.pipeline.publication import (
    classify_publication_blockers,
    refresh_publication_impact,
)
from ydbdoc_review.pipeline.skip_paths import filter_path_set, filter_translate_changes
from ydbdoc_review.pipeline.translation_preflight import preflight_translation
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    FinalTreeBlocker,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    build_commit_message,
    build_full_report,
    build_source_pr_comment,
    build_translation_pr_body,
    build_verify_fixup_pr_body,
    build_verify_fixup_source_comment,
    parse_final_tree_blocker_manifest,
    result_has_blocking_findings,
)
from ydbdoc_review.reporting.locations import ReportLinkContext
from ydbdoc_review.reporting.provenance_drift import build_later_ru_drift_report
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.coverage import (
    CoverageEvidence,
    CoverageEvidenceBindingMiss,
    build_coverage_evidence,
    load_coverage_evidence,
    plan_source_coverage,
    save_coverage_evidence,
    validate_coverage_evidence,
)
from ydbdoc_review.translation.critic import run_critic as run_coverage_critic
from ydbdoc_review.translation.glossary import Glossary, load_glossary
from ydbdoc_review.translation.prompts import load_template
from ydbdoc_review.validation.en_link_targets import (
    _mask_yfm_include_directives,
    apply_en_link_target_checks,
    check_en_page_link_targets,
)
from ydbdoc_review.validation.glossary_toc_links import (
    build_en_toc_reachable_from_repo,
    resolve_internal_md_href,
)
from ydbdoc_review.validation.href_parity import (
    _MD_LINK,
    _is_internal_href,
    check_outbound_fragments,
    propose_frozen_baseline_link_wrapper_repair,
)
from ydbdoc_review.validation.include_targets import (
    apply_include_parity_repair,
    apply_include_target_checks,
)
from ydbdoc_review.validation.redirect_impacts import (
    added_redirects,
    mirror_redirects_to_en,
    retarget_redirect_inbound_links,
)
from ydbdoc_review.validation.toc_targets import (
    apply_orphan_toc_page_checks,
    apply_toc_target_checks,
)

logger = logging.getLogger(__name__)

_GITHUB_ACTOR_NAME = "github-actions[bot]"
_GITHUB_ACTOR_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
_REPORT_MARKER = "ydbdoc-review — отчёт"
_ACTIVE_VERIFY_RERUN_CAPABILITY: ContextVar[object | None] = ContextVar(
    "ydbdoc_review_active_verify_rerun_capability",
    default=None,
)


def _merge_yellow_warnings(
    result: PRTranslationResult,
    warnings: tuple[str, ...] | list[str],
) -> None:
    """Carry nonblocking warnings across planner/workflow result boundaries."""
    result.yellow_warnings = list(
        dict.fromkeys([*result.yellow_warnings, *warnings])
    )


@dataclass(frozen=True)
class TouchedPaths:
    """Paths written or removed by ``doc_translate`` / ``doc_verify``."""

    written: list[str]
    deleted: list[str]

    def __bool__(self) -> bool:
        return bool(self.written or self.deleted)


@dataclass
class DocJobResult:
    """Outcome of ``run_doc_translate`` or ``run_doc_verify``."""

    mode: str
    pr_number: int
    source_pr_number: int | None = None
    translation_branch: str | None = None
    translation_pr_number: int | None = None
    translation_pr_url: str | None = None
    source_comment_url: str | None = None
    translation_comment_url: str | None = None
    pr_result: PRTranslationResult = field(default_factory=PRTranslationResult)
    committed: bool = False
    pushed: bool = False
    dry_run: bool = False
    # Ops deny, continue refused, or other hard stop (§2 / §11).
    blocked: bool = False


@dataclass(frozen=True)
class _BoundArtifactPR:
    """An existing artifact PR bound to one immutable remote branch SHA."""

    url: str
    number: int
    bound_sha: str
    provenance: TranslationArtifactProvenance


def _snapshot_destination_lease(
    gh: GitHubClient,
    owner: str,
    repo: str,
    branch: str,
) -> RemoteRefLease:
    return RemoteRefLease(
        branch=branch,
        expected_sha=gh.get_branch_sha(owner, repo, branch),
    )


def _await_inline_fixup_pr_context(
    gh: GitHubClient,
    owner: str,
    repo: str,
    pr_number: int,
    *,
    repo_path: str,
    previous_context: PullRequestContext,
    expected_sha: str,
) -> PullRequestContext:
    if (
        not expected_sha
        or len(expected_sha) != 40
        or any(char not in "0123456789abcdef" for char in expected_sha)
        or not previous_context.head_sha
        or expected_sha == previous_context.head_sha
        or previous_context.owner != owner
        or previous_context.repo != repo
        or previous_context.number != pr_number
        or not previous_context.head_ref
        or previous_context.state != "open"
        or previous_context.merged
    ):
        raise RuntimeError("invalid inline fixup PR-context preconditions")

    delays = (1.0, 2.0, 4.0, 8.0, 15.0)
    for attempt in range(6):
        local_head = resolve_commit_ref(repo_path, "HEAD")
        if local_head != expected_sha:
            raise RuntimeError(
                "inline verify PR-head visibility checkout changed before REST read: "
                f"expected {expected_sha}, found {local_head}"
            )
        _require_remote_sha(
            gh,
            owner,
            repo,
            previous_context.head_ref,
            expected_sha,
            context="inline verify PR-head visibility before REST read",
        )

        fresh = pull_request_context(gh, owner, repo, pr_number)
        previous_identity = (
            previous_context.owner,
            previous_context.repo,
            previous_context.number,
            previous_context.head_ref,
            previous_context.head_repo_full_name,
            previous_context.head_repo_https_url,
            previous_context.base_ref,
        )
        fresh_identity = (
            fresh.owner,
            fresh.repo,
            fresh.number,
            fresh.head_ref,
            fresh.head_repo_full_name,
            fresh.head_repo_https_url,
            fresh.base_ref,
        )
        if (
            fresh_identity != previous_identity
            or fresh.state != "open"
            or fresh.merged
        ):
            raise RuntimeError(
                "inline verify PR identity or publication inputs changed while "
                "waiting for head visibility"
            )
        if fresh.body != previous_context.body:
            raise ValueError(
                "inline verify PR authority evidence/body changed while "
                "waiting for head visibility"
            )
        if fresh.head_sha not in {previous_context.head_sha, expected_sha}:
            raise RuntimeError(
                "inline verify PR head changed to an unexpected SHA while waiting "
                f"for visibility: expected {previous_context.head_sha} or "
                f"{expected_sha}, found {fresh.head_sha or '<missing>'}"
            )

        _require_remote_sha(
            gh,
            owner,
            repo,
            previous_context.head_ref,
            expected_sha,
            context="inline verify PR-head visibility after REST read",
        )
        local_head = resolve_commit_ref(repo_path, "HEAD")
        if local_head != expected_sha:
            raise RuntimeError(
                "inline verify PR-head visibility checkout changed during REST read: "
                f"expected {expected_sha}, found {local_head}"
            )

        if fresh.head_sha == expected_sha:
            return fresh
        if attempt == 5:
            raise RuntimeError(
                "inline verify PR head visibility did not converge for "
                f"PR #{pr_number}: expected {expected_sha}, last observed "
                f"{fresh.head_sha or '<missing>'} after {attempt + 1} attempts; "
                "the already published branch is retained"
            )
        delay = delays[attempt]
        logger.info(
            "Waiting for PR #%s head visibility (%s/6): old=%s expected=%s; "
            "retrying in %.1fs",
            pr_number,
            attempt + 1,
            previous_context.head_sha,
            expected_sha,
            delay,
        )
        time.sleep(delay)

    raise AssertionError("unreachable inline fixup PR-context wait")


def _freeze_candidate_sha(repo_path: str) -> str:
    candidate = git_head_sha(repo_path)
    if not candidate:
        raise RuntimeError("cannot publish branch without a candidate commit SHA")
    try:
        resolved = resolve_commit_ref(repo_path, candidate)
    except RuntimeError as exc:
        raise RuntimeError(
            f"cannot publish invalid candidate commit SHA {candidate!r}"
        ) from exc
    if resolved != candidate:
        raise RuntimeError(
            f"candidate commit SHA changed while freezing it: {candidate} -> {resolved}"
        )
    return candidate


def _require_remote_sha(
    gh: GitHubClient,
    owner: str,
    repo: str,
    branch: str,
    expected_sha: str | None,
    *,
    context: str,
) -> None:
    actual = gh.get_branch_sha(owner, repo, branch)
    if actual != expected_sha:
        raise RuntimeError(
            f"{context}: remote branch {branch} changed: "
            f"expected {expected_sha or '<absent>'}, found {actual or '<absent>'}"
        )


def _raise_with_owned_rollback(
    original_error: Exception,
    receipt: RefMutationReceipt | None,
    previous_sha: str | None,
    *,
    repo_path: str,
    branch: str,
    push_token: str,
    upstream_url: str,
    message: str,
) -> NoReturn:
    if receipt is None or receipt.status is not RefMutationStatus.CHANGED:
        raise original_error
    requested_sha = receipt.requested_sha
    if not requested_sha:
        raise original_error
    try:
        rollback_pushed_branch(
            repo_path,
            "ydbdoc-review-push",
            branch,
            push_token,
            upstream_url,
            expected_pushed_sha=requested_sha,
            previous_sha=previous_sha,
        )
    except Exception as rollback_error:
        raise ExceptionGroup(
            message,
            [original_error, rollback_error],
        ) from None
    raise original_error


def _github_tokens(config: Config) -> tuple[str, str]:
    api = config.secrets.github_token
    push = config.secrets.github_push_token or api
    if not api:
        raise GitHubConfigError("GitHub token not configured. Set GITHUB_TOKEN.")
    if not push:
        raise GitHubConfigError(
            "GitHub push token not configured. Set GITHUB_PUSH_TOKEN or GITHUB_TOKEN."
        )
    return api, push


def _next_report_number(client: GitHubClient, owner: str, repo: str, issue_number: int) -> int:
    count = 0
    for comment in client.iter_issue_comments(owner, repo, issue_number):
        body = str(comment.get("body") or "")
        if _REPORT_MARKER in body:
            count += 1
    return count + 1


def _enforce_report_checkout_bytes(
    repo_path: str,
    checkout_ref: str,
    result: PRTranslationResult,
) -> list[str]:
    """Block a report whose in-memory EN differs from its advertised SHA."""
    mismatches: list[str] = []
    for pair in result.pair_results:
        if pair.deleted or pair.target_text is None:
            continue
        committed = read_text_at_commit(repo_path, checkout_ref, pair.plan.target_path)
        if committed == pair.target_text:
            continue
        mismatches.append(pair.plan.target_path)
        if pair.file_result is not None:
            pair.file_result.verdict = "blocked"
            pair.file_result.heuristic_blocking.append(
                "report_checkout_mismatch: final QA text differs from immutable "
                f"checkout `{checkout_ref[:12]}`"
            )
    return mismatches


def _safe_post_issue_comment(
    gh: GitHubClient,
    owner: str,
    repo: str,
    issue_number: int,
    body: str,
    *,
    label: str,
) -> str | None:
    """Post a PR/issue comment; log and return None instead of aborting the job."""
    try:
        return gh.post_issue_comment(owner, repo, issue_number, body)
    except GitHubAPIError as exc:
        logger.warning(
            "Could not post %s comment on %s/%s#%s: %s",
            label,
            owner,
            repo,
            issue_number,
            exc,
        )
        return None


def _pr_result_for_bilingual_skips(
    en_paths: frozenset[str] | set[str],
    *,
    docs_root: str,
) -> PRTranslationResult:
    """Synthetic skipped pair results so §6.76 source comment can fire (#48751)."""
    results: list[PairRunResult] = []
    for en_path in sorted(en_paths):
        if not en_path.endswith(".md"):
            continue
        ru_path = counterpart(en_path, docs_root)
        if ru_path is None:
            continue
        pair = DocPair(
            ru_path=ru_path,
            en_path=en_path,
            ru_changed=True,
            en_changed=True,
        )
        plan = PairPlan(
            pair=pair,
            action="skip",
            source_path=ru_path,
            target_path=en_path,
            source_lang="ru",
            target_lang="en",
            summary=BILINGUAL_SKIP_SUMMARY,
        )
        results.append(PairRunResult(plan=plan, skipped=True))
    return PRTranslationResult(pair_results=results)


def _delete_stale_verify_fixup(
    gh: GitHubClient,
    owner: str,
    repo: str,
    fixup_branch: str,
) -> None:
    """Remove ``ydbdoc-review/verify-*`` so a re-run starts clean (§6.136).

    Deleting the head branch also closes any open fixup PR that used it.
    """
    if gh.delete_branch(owner, repo, fixup_branch):
        logger.info(
            "Deleted stale doc_verify fixup branch %s before run/push",
            fixup_branch,
        )


def _apply_results_to_disk(
    repo_path: str,
    result: PRTranslationResult,
    *,
    dry_run: bool,
    docs_root: str = "ydb/docs",
) -> TouchedPaths:
    """Write translated markdown, navigation YAML, locale assets, and deletes."""
    from ydbdoc_review.translation.file_profiles import is_glossary_file
    from ydbdoc_review.validation.locale_assets import apply_locale_asset_copies

    written: list[str] = []
    deleted: list[str] = []
    for run in result.pair_results:
        if run.skipped or run.error:
            continue
        rel = run.plan.target_path
        # Glossary hub: never rewrite EN from verify (critic/finalize can
        # hybridize 400+ segments; §6.189 / #49578).
        if run.plan.action == "critic_only" and is_glossary_file(rel):
            continue
        if run.deleted:
            deleted.append(rel)
            if dry_run:
                continue
            path = Path(repo_path) / rel.replace("/", os.sep)
            if path.is_file():
                path.unlink()
            continue
        if run.target_text is None:
            continue
        # §6.232: critic_only no-ops must not enter ``written``. Inline verify
        # restages those paths onto the remote tip; overlaying unchanged
        # checkout bytes clobbers concurrent tip fixes (e.g. a manual href
        # repair pushed while this job still held an older SHA).
        if run.plan.action == "critic_only":
            on_disk = read_text(repo_path, rel)
            if on_disk is not None and on_disk == run.target_text:
                continue
        written.append(rel)
        if not dry_run:
            write_text(repo_path, rel, run.target_text)
    for nav in result.navigation_results:
        if nav.error or nav.target_text is None:
            continue
        rel = nav.en_path
        written.append(rel)
        if not dry_run:
            write_text(repo_path, rel, nav.target_text)
    written.extend(
        apply_locale_asset_copies(
            result,
            repo_path=repo_path,
            docs_root=docs_root,
            dry_run=dry_run,
        )
    )
    return TouchedPaths(written=list(dict.fromkeys(written)), deleted=deleted)


def _collect_frozen_baseline_wrapper_repairs(
    repo_path: str,
    result: PRTranslationResult,
    *,
    provenance: TranslationArtifactProvenance,
    verified_commit_sha: str,
    source_scope_en: frozenset[str],
    docs_root: str,
) -> dict[str, str]:
    """Return only fully proven wrapper repairs for existing scoped EN pages."""
    authority = provenance.authority
    root = docs_root.strip("/")

    def read_baseline(path: str) -> str | None:
        normalized = path.replace("\\", "/")
        if normalized != f"{root}/redirects.yaml" and not normalized.startswith(
            f"{root}/en/"
        ):
            raise ValueError(f"wrapper repair B read outside EN namespace: {normalized}")
        return read_text_at_commit(
            repo_path, authority.baseline_sha, normalized
        )

    def read_final(path: str) -> str | None:
        normalized = path.replace("\\", "/")
        if not normalized.startswith(f"{root}/en/"):
            raise ValueError(f"wrapper repair K read outside EN namespace: {normalized}")
        return read_text_at_commit(repo_path, verified_commit_sha, normalized)

    proposals: dict[str, str] = {}
    for run in sorted(result.pair_results, key=lambda item: item.plan.target_path):
        path = run.plan.target_path.replace("\\", "/")
        fr = run.file_result
        if (
            path not in source_scope_en
            or not path.startswith(f"{root}/en/")
            or not path.endswith(".md")
            or run.plan.target_lang != "en"
            or run.skipped
            or run.deleted
            or run.error is not None
            or run.soft_keep_reason is not None
            or fr is None
            or fr.segment_alignment_error is not None
            or bool(fr.manual_actions)
            or bool(fr.heuristic_blocking)
            or (
                fr.critic_unresolved is not None
                and fr.critic_unresolved.verdict == "blocked"
            )
        ):
            continue
        current = read_text_at_commit(repo_path, verified_commit_sha, path)
        if not current or run.target_text != current:
            continue
        ru_path = run.plan.source_path.replace("\\", "/")
        if not ru_path.startswith(f"{root}/ru/") or not ru_path.endswith(".md"):
            continue
        source_base = read_text_at_commit(
            repo_path, authority.source_base_sha, ru_path
        )
        source_head = read_text_at_commit(
            repo_path, authority.source_head_sha, ru_path
        )
        source_selected = read_text_at_commit(repo_path, authority.ru_sha, ru_path)
        baseline = read_text_at_commit(repo_path, authority.baseline_sha, path)
        if (
            not source_base
            or not source_head
            or not source_selected
            or not baseline
            or run.source_text != source_selected
        ):
            continue
        issues = [*run.validation_issues, *fr.link_contract_issues]
        if not issues or any(
            issue.code != "missing_link_wrapper" or not issue.href
            for issue in issues
        ):
            continue
        hrefs = {issue.href for issue in issues}
        if len(hrefs) != 1:
            continue
        proposed = propose_frozen_baseline_link_wrapper_repair(
            current,
            source_head,
            source_base_text=source_base,
            target_baseline_text=baseline,
            missing_href=next(iter(hrefs)),
            en_page_path=path,
            read_baseline=read_baseline,
            read_final=read_final,
            docs_root=docs_root,
        )
        if proposed is not None:
            proposals[path] = proposed
    return proposals


def _soft_keep_message(reason: str) -> str:
    normalized = " ".join(reason.split()) or "translation failed"
    return (
        f"translation_soft_keep: {normalized}. Действие: вручную обновить EN "
        "в этой ветке, затем запустить doc_verify"
    )


def _materialize_soft_keep_blockers(
    result: PRTranslationResult,
    *,
    repo_path: str,
    baseline_ref: str,
) -> None:
    """Accept only typed soft-keeps retaining exact non-empty existing EN bytes."""
    blockers = [
        blocker
        for blocker in result.final_tree_blockers
        if blocker.code != "translation_soft_keep"
    ]
    for run in result.pair_results:
        if run.soft_keep_reason is None:
            continue
        target = run.target_text or ""
        existing = read_text_at_commit(repo_path, baseline_ref, run.plan.target_path)
        if (
            run.plan.action != "translate_to_en"
            or not target.strip()
            or not existing
            or target != existing
        ):
            run.error = (
                "translation_soft_keep: retained target is not a non-empty "
                "materialized existing EN file"
            )
            continue
        blockers.append(
            FinalTreeBlocker(
                path=run.plan.target_path,
                code="translation_soft_keep",
                message=_soft_keep_message(run.soft_keep_reason),
                artifact_sha256=hashlib.sha256(target.encode()).hexdigest(),
            )
        )
    result.final_tree_blockers = list(dict.fromkeys(blockers))


def _freeze_soft_keep_artifact_hashes(
    result: PRTranslationResult,
    *,
    repo_path: str,
) -> None:
    """Bind soft-keep blockers to exact bytes after every final-tree repair."""
    frozen: list[FinalTreeBlocker] = []
    for blocker in result.final_tree_blockers:
        if blocker.code != "translation_soft_keep":
            frozen.append(blocker)
            continue
        path = Path(repo_path) / blocker.path.replace("/", os.sep)
        if not path.is_file():
            for run in result.pair_results:
                if run.plan.target_path == blocker.path:
                    run.error = "translation_soft_keep: published target is missing"
                    break
            continue
        artifact = path.read_bytes()
        if not artifact.strip():
            for run in result.pair_results:
                if run.plan.target_path == blocker.path:
                    run.error = "translation_soft_keep: published target is empty"
                    break
            continue
        frozen.append(
            FinalTreeBlocker(
                path=blocker.path,
                code=blocker.code,
                message=blocker.message,
                artifact_sha256=hashlib.sha256(artifact).hexdigest(),
            )
        )
    result.final_tree_blockers = frozen


def _soft_keep_is_manually_resolved(
    blocker: FinalTreeBlocker,
    result: PRTranslationResult,
    *,
    repo_path: str,
) -> bool:
    """Clear only after changed bytes were verified by a current safe pair run."""
    path = Path(repo_path) / blocker.path.replace("/", os.sep)
    if not path.is_file():
        return False
    artifact = path.read_bytes()
    if not artifact.strip() or hashlib.sha256(artifact).hexdigest() == blocker.artifact_sha256:
        return False
    matching = [
        run
        for run in result.pair_results
        if run.plan.target_path.replace("\\", "/") == blocker.path.replace("\\", "/")
        and run.target_text is not None
        and run.file_result is not None
        and not run.error
        and not run.skipped
        and not run.deleted
    ]
    if len(matching) != 1 or matching[0].target_text.encode() != artifact:
        return False
    classified = classify_publication_blockers(
        PRTranslationResult(pair_results=matching)
    )
    return not classified.incomplete and not classified.unsafe


def _matching_existing_soft_keep_artifact_pr(
    gh: GitHubClient,
    owner: str,
    repo: str,
    *,
    branch: str,
    base: str,
    result: PRTranslationResult,
    expected_remote_sha: str | None,
    repo_path: str,
    source_repo: str,
    source_pr: int,
) -> _BoundArtifactPR | None:
    """Return an open PR only when its current head carries the same RED evidence."""
    if not expected_remote_sha:
        return None
    found = gh.find_open_pull_by_head(
        owner,
        repo,
        head_branch=branch,
        base=base,
    )
    if found is None:
        return None
    url, number = found
    pull = gh.get_pull(owner, repo, number)
    head = pull.get("head") or {}
    if (
        not isinstance(head, dict)
        or str(head.get("sha") or "") != expected_remote_sha
    ):
        return None
    try:
        provenance = validate_authority_evidence(
            repo_path,
            parse_authority_evidence(str(pull.get("body") or "")),
            expected_repo=source_repo,
            expected_source_pr=source_pr,
            current_candidate_sha=expected_remote_sha,
        )
        published_blockers = parse_final_tree_blocker_manifest(
            str(pull.get("body") or "")
        )
    except (RuntimeError, ValueError):
        return None

    def artifact_key(blocker: FinalTreeBlocker) -> tuple[str, str, str | None]:
        detail = (
            blocker.artifact_sha256
            if blocker.code == "translation_soft_keep"
            else blocker.message
        )
        return blocker.code, blocker.path.replace("\\", "/"), detail

    expected = {artifact_key(blocker) for blocker in result.final_tree_blockers}
    actual = {artifact_key(blocker) for blocker in published_blockers}
    if not expected or expected != actual:
        return None
    return _BoundArtifactPR(
        url=url,
        number=number,
        bound_sha=expected_remote_sha,
        provenance=provenance,
    )


def _restore_out_of_scope_en_from_base(
    repo_path: str,
    *,
    changes: list[tuple[str, str]],
    allowed_en_paths: frozenset[str],
    merge_base_with: str,
    docs_root: str,
    dry_run: bool,
) -> list[str]:
    """Reset tip-ambient EN outside source-PR scope to ``merge_base_with`` (§6.240).

    Translation branches sometimes accumulate EN pages that differ from main but
    are not twins of the source PR. Leaving them in the tip makes every verify
    expand into unrelated RU/EN drift; restoring them keeps the PR scoped.
    """
    if not allowed_en_paths:
        return []
    root = docs_root.strip("/")
    restored: list[str] = []
    for raw_path, _kind in changes:
        path = raw_path.replace("\\", "/")
        if not path.startswith(f"{root}/en/") or not path.endswith(".md"):
            continue
        if path in allowed_en_paths:
            continue
        base_text = read_text_at_commit(repo_path, merge_base_with, path)
        work_text = read_text(repo_path, path)
        if base_text is None:
            continue
        if work_text == base_text:
            continue
        if not dry_run:
            write_text(repo_path, path, base_text)
        restored.append(path)
    if restored:
        logger.info(
            "Restored %s tip-ambient EN path(s) to %s (outside source PR scope)",
            len(restored),
            merge_base_with,
        )
    return restored


def _docs_text_reader(
    repo_path: str,
    content_ref: str,
    *,
    authority=None,
    docs_root: str = "ydb/docs",
):
    """Read docs paths from the commit captured when this reader is created."""
    content_sha = resolve_commit_ref(repo_path, content_ref)
    ru_sha = resolve_commit_ref(repo_path, authority.ru_sha) if authority is not None else None
    baseline_sha = (
        resolve_commit_ref(repo_path, authority.baseline_sha)
        if authority is not None
        else None
    )
    root = docs_root.strip("/")

    def _read(path: str) -> str | None:
        normalized = path.replace("\\", "/")
        selected = content_sha
        if authority is not None:
            if normalized == f"{root}/redirects.yaml":
                selected = baseline_sha
            elif normalized.startswith(f"{root}/ru/"):
                selected = ru_sha
        return read_text_at_commit(repo_path, selected, normalized)

    return _read


def _final_tree_reader(
    repo_path: str,
    base_ref: str,
    overlay_paths: set[str] | frozenset[str],
    *,
    deleted_paths: set[str] | frozenset[str] = frozenset(),
):
    """Read the intended post-translate docs tree (§6.229).

    Explicit same-job deletions are tombstones. Declared overlays must exist on
    disk and supply their exact bytes. Every other path comes only from the
    immutable base captured when this reader is created.
    """
    base_sha = resolve_commit_ref(repo_path, base_ref)
    overlays = {p.replace("\\", "/") for p in overlay_paths}
    tombstones = {p.replace("\\", "/") for p in deleted_paths}

    def _read(path: str) -> str | None:
        norm = path.replace("\\", "/")
        if norm in tombstones:
            return None
        if norm in overlays:
            overlay = Path(repo_path) / norm.replace("/", os.sep)
            if not overlay.is_file():
                raise RuntimeError(f"declared overlay is missing from disk: {norm}")
            try:
                return overlay.read_bytes().decode("utf-8")
            except OSError as exc:
                raise RuntimeError(f"could not read declared overlay: {norm}") from exc
            except UnicodeDecodeError as exc:
                raise RuntimeError(f"declared overlay is not valid UTF-8: {norm}") from exc
        return read_text_at_commit(repo_path, base_sha, norm)

    return _read


@dataclass(frozen=True)
class _OutboundFragmentOccurrence:
    message: str
    href: str
    target_path: str
    raw_fragment: str
    decoded_fragment: str
    expected_link_message: str


@dataclass
class _DeferredOutboundFragments:
    file_result: FileTranslationResult
    page_path: str
    baseline_text: str | None
    original_blocking: list[str]
    occurrences: list[_OutboundFragmentOccurrence]
    reattached_messages: list[str] = field(default_factory=list)


def _outbound_fragment_identity(
    page_path: str,
    href: str,
) -> tuple[str, str, str] | None:
    """Return the full target identity shared by both outbound link gates."""
    if not _is_internal_href(href):
        return None
    path_part, marker, raw_fragment = href.partition("#")
    if not marker or not raw_fragment or (path_part and not path_part.endswith(".md")):
        return None
    target_path = (
        page_path
        if not path_part
        else resolve_internal_md_href(page_path, href)
    )
    if target_path is None:
        return None
    return target_path.replace("\\", "/"), raw_fragment, unquote(raw_fragment)


def _isolated_markdown_link(text: str, start: int, end: int) -> str:
    """Keep one link and line offsets while masking every other character."""
    chars = ["\n" if char == "\n" else "x" for char in text]
    chars[start:end] = text[start:end]
    return "".join(chars)


def _proven_outbound_fragment_occurrences(
    page_path: str,
    text: str,
    *,
    read_docs,
    baseline_text: str | None,
    baseline_read_text=None,
) -> list[_OutboundFragmentOccurrence]:
    """Find outbound findings covered by the final visible-Markdown gate."""
    canonical_remaining = check_outbound_fragments(
        page_path,
        text,
        read_text=read_docs,
        en_baseline_text=baseline_text,
    )
    final_gate_remaining = check_en_page_link_targets(
        page_path,
        text,
        read_text=read_docs,
        baseline_text=baseline_text,
        baseline_read_text=baseline_read_text,
    )
    if not canonical_remaining or not final_gate_remaining:
        return []

    proven: list[_OutboundFragmentOccurrence] = []
    masked = _mask_yfm_include_directives(text)
    for match in _MD_LINK.finditer(masked):
        href = match.group(2).strip()
        identity = _outbound_fragment_identity(page_path, href)
        if identity is None:
            continue
        isolated = _isolated_markdown_link(text, match.start(), match.end())
        outbound = check_outbound_fragments(
            page_path,
            isolated,
            read_text=read_docs,
            en_baseline_text=baseline_text,
        )
        link_target = check_en_page_link_targets(
            page_path,
            isolated,
            read_text=read_docs,
        )
        if len(outbound) != 1 or len(link_target) != 1:
            continue
        message = outbound[0]
        expected_link_message = link_target[0]
        if message not in canonical_remaining:
            continue
        if expected_link_message not in final_gate_remaining:
            continue
        canonical_remaining.remove(message)
        final_gate_remaining.remove(expected_link_message)
        target_path, raw_fragment, decoded_fragment = identity
        proven.append(
            _OutboundFragmentOccurrence(
                message=message,
                href=href,
                target_path=target_path,
                raw_fragment=raw_fragment,
                decoded_fragment=decoded_fragment,
                expected_link_message=expected_link_message,
            )
        )
    return proven


def _defer_proven_outbound_fragments(
    result: PRTranslationResult,
    *,
    repo_path: str,
    baseline_ref: str,
) -> list[_DeferredOutboundFragments]:
    """Detach only active EN Markdown findings covered by the final gate."""
    read_docs = _docs_text_reader(repo_path, baseline_ref)
    deferred: list[_DeferredOutboundFragments] = []
    for run in result.pair_results:
        file_result = run.file_result
        if (
            run.skipped
            or run.deleted
            or run.error
            or run.plan.target_lang != "en"
            or not run.plan.target_path.endswith(".md")
            or run.target_text is None
            or file_result is None
        ):
            continue
        page_path = run.plan.target_path.replace("\\", "/")
        baseline_text = read_text_at_commit(repo_path, baseline_ref, page_path)
        proven = _proven_outbound_fragment_occurrences(
            page_path,
            run.target_text,
            read_docs=read_docs,
            baseline_text=baseline_text,
            baseline_read_text=lambda path: read_text_at_commit(
                repo_path, baseline_ref, path
            ),
        )
        available = list(file_result.heuristic_blocking)
        selected: list[_OutboundFragmentOccurrence] = []
        for occurrence in proven:
            if occurrence.message not in available:
                continue
            available.remove(occurrence.message)
            selected.append(occurrence)
        if not selected:
            continue

        original = list(file_result.heuristic_blocking)
        to_remove = [occurrence.message for occurrence in selected]
        retained: list[str] = []
        for message in original:
            if message in to_remove:
                to_remove.remove(message)
            else:
                retained.append(message)
        file_result.heuristic_blocking[:] = retained
        deferred.append(
            _DeferredOutboundFragments(
                file_result=file_result,
                page_path=page_path,
                baseline_text=baseline_text,
                original_blocking=original,
                occurrences=selected,
            )
        )
    return deferred


def _restore_deferred_outbound_fragments(
    deferred: list[_DeferredOutboundFragments],
) -> None:
    """Restore the exact early blocker lists after an unrelated early veto."""
    for bundle in deferred:
        bundle.file_result.heuristic_blocking[:] = bundle.original_blocking
        bundle.reattached_messages.clear()


def _recheck_deferred_outbound_fragments(
    result: PRTranslationResult,
    deferred: list[_DeferredOutboundFragments],
    *,
    read_final_docs,
    baseline_read_text=None,
) -> None:
    """Replace provisional findings only with exact final-tree evidence."""
    for bundle in deferred:
        for message in reversed(bundle.reattached_messages):
            for index in range(
                len(bundle.file_result.heuristic_blocking) - 1,
                -1,
                -1,
            ):
                if bundle.file_result.heuristic_blocking[index] == message:
                    bundle.file_result.heuristic_blocking.pop(index)
                    break
        bundle.reattached_messages.clear()

        final_text = read_final_docs(bundle.page_path)
        if not final_text:
            bundle.reattached_messages.extend(
                occurrence.message for occurrence in bundle.occurrences
            )
            bundle.file_result.heuristic_blocking.extend(bundle.reattached_messages)
            continue

        canonical_remaining = check_outbound_fragments(
            bundle.page_path,
            final_text,
            read_text=read_final_docs,
            en_baseline_text=bundle.baseline_text,
        )
        proven_remaining = _proven_outbound_fragment_occurrences(
            bundle.page_path,
            final_text,
            read_docs=read_final_docs,
            baseline_text=bundle.baseline_text,
            baseline_read_text=baseline_read_text,
        )
        for original in bundle.occurrences:
            current_message: str | None = None
            for message in canonical_remaining:
                probe = check_outbound_fragments(
                    bundle.page_path,
                    f"[deferred]({original.href})",
                    read_text=read_final_docs,
                    en_baseline_text=bundle.baseline_text,
                )
                if len(probe) != 1 or message != probe[0]:
                    continue
                current_message = message
                canonical_remaining.remove(message)
                break
            if current_message is None:
                continue

            exact_final: _OutboundFragmentOccurrence | None = None
            for candidate in proven_remaining:
                if (
                    candidate.message == current_message
                    and candidate.href == original.href
                    and candidate.target_path == original.target_path
                    and candidate.raw_fragment == original.raw_fragment
                    and candidate.decoded_fragment == original.decoded_fragment
                ):
                    exact_final = candidate
                    proven_remaining.remove(candidate)
                    break
            typed_replacement = exact_final is not None and any(
                blocker.code == "en_link_target"
                and blocker.path.replace("\\", "/") == bundle.page_path
                and blocker.message == exact_final.expected_link_message
                for blocker in result.final_tree_blockers
            )
            if not typed_replacement:
                bundle.reattached_messages.append(current_message)
        bundle.file_result.heuristic_blocking.extend(bundle.reattached_messages)


def _repair_en_fragments_after_apply(
    repo_path: str,
    paths: list[str],
    *,
    dry_run: bool,
    merge_base_with: str | None = None,
    ru_content_ref: str | None = None,
    docs_root: str = "ydb/docs",
) -> list[str]:
    """Re-run fragment repair once all EN targets exist on disk (§6.225).

    Pair-level ``repair_en_fragments`` can run before a newly translated EN
    target page is written, leaving RU legacy translit fragments in place.
    Inbound redirect retarget also skips links whose path already points at
    the redirect ``to`` target. A final pass over written EN pages fixes both.

    When ``merge_base_with`` is set, target resolution uses the §6.229 final
    tree (tip + overlays) so tip-preserved pages are not rewritten against a
    stale merge-commit worktree.
    """
    from ydbdoc_review.validation.fragment_repair import repair_en_fragments

    overlay = {p.replace("\\", "/") for p in paths}
    if merge_base_with:
        read_final = _final_tree_reader(repo_path, merge_base_with, overlay)
    else:

        def read_final(path: str) -> str | None:
            return read_text(repo_path, path)

    ru_sha = (
        resolve_commit_ref(repo_path, ru_content_ref)
        if ru_content_ref is not None
        else None
    )
    root = docs_root.strip("/")

    def _read(path: str) -> str | None:
        normalized = path.replace("\\", "/")
        if ru_sha is not None and normalized.startswith(f"{root}/ru/"):
            return read_text_at_commit(repo_path, ru_sha, normalized)
        return read_final(normalized)

    repaired: list[str] = []
    for rel in paths:
        if not rel.endswith(".md") or "/docs/en/" not in rel:
            continue
        # Always edit the bytes we wrote (worktree), not tip.
        en_text = read_text(repo_path, rel)
        if not en_text:
            continue
        ru_twin = rel.replace("/docs/en/", "/docs/ru/", 1)
        fixed = repair_en_fragments(
            en_text,
            en_page_path=rel,
            read_text=_read,
            ru_source=_read(ru_twin),
            en_baseline=(
                read_text_at_commit(repo_path, merge_base_with, rel)
                if merge_base_with
                else None
            ),
        )
        if fixed == en_text:
            continue
        repaired.append(rel)
        if not dry_run:
            write_text(repo_path, rel, fixed)
    return repaired


def _reconcile_final_en_same_fragment_paths_after_apply(
    repo_path: str,
    contents: list[PairContent],
    result: PRTranslationResult,
    paths: list[str],
    *,
    dry_run: bool,
    merge_base_with: str | None = None,
    ru_content_ref: str | None = None,
    deleted_paths: list[str] | None = None,
) -> list[str]:
    """Apply fail-closed four-snapshot same-fragment path preservation.

    This runs after declaration and generic late repair, when target checks can
    see the complete tip-plus-overlay EN tree.  It never invents anchors or
    weakens the final link gate.
    """
    from ydbdoc_review.validation.href_parity import (
        reconcile_final_en_same_fragment_paths,
    )

    if not merge_base_with or not ru_content_ref:
        return []
    normalized_paths = {path.replace("\\", "/") for path in paths}
    final_reader = _final_tree_reader(
        repo_path,
        merge_base_with,
        normalized_paths,
        deleted_paths=set(deleted_paths or ()),
    )

    def source_reader(path: str) -> str | None:
        return read_text_at_commit(repo_path, ru_content_ref, path)

    issues_by_path: dict[str, tuple] = {}
    for run in result.pair_results:
        path = run.plan.target_path.replace("\\", "/")
        if path not in normalized_paths:
            continue
        issues_by_path[path] = run.validation_issues

    reconciled: list[str] = []
    for content in contents:
        en_path = content.pair.en_path.replace("\\", "/")
        if en_path not in normalized_paths or not en_path.endswith(".md"):
            continue
        candidate = read_text(repo_path, en_path)
        if candidate is None:
            continue
        # E0 is strictly the tip EN snapshot. A historical checkout body is
        # not lineage evidence when this path is absent from ``merge_base``.
        tip_en = read_text_at_commit(repo_path, merge_base_with, en_path)
        if tip_en is None:
            continue
        fixed = reconcile_final_en_same_fragment_paths(
            content.ru_base_text,
            content.ru_text,
            tip_en,
            candidate,
            ru_page_path=content.pair.ru_path,
            en_page_path=en_path,
            read_source_ru=source_reader,
            read_final_en=final_reader,
            link_contract_issues=issues_by_path.get(en_path, ()),
        )
        if fixed == candidate:
            continue
        reconciled.append(en_path)
        if not dry_run:
            write_text(repo_path, en_path, fixed)
    return reconciled


@dataclass(frozen=True)
class _ExactFragmentDeclarationProposal:
    ru_owner_path: str
    en_owner_path: str
    before_en_text: str
    after_en_text: str
    fragments: tuple[str, ...]


@dataclass(frozen=True)
class _AmbiguousExactFragmentOwner:
    ru_owner_path: str
    en_owner_path: str
    reason: str


def _discover_exact_ascii_fragment_declaration_proposals(
    paths: Iterable[str],
    *,
    read_page: Callable[[str], str | None],
    read_candidate: Callable[[str], str | None],
    docs_root: str = "ydb/docs",
    redirects_yaml: str | None = None,
    ambiguous_out: list[_AmbiguousExactFragmentOwner] | None = None,
) -> tuple[_ExactFragmentDeclarationProposal, ...]:
    """Prove exact declaration repairs without performing writes."""
    from ydbdoc_review.parsing.include_paths import collect_yfm_includes, resolve_locale_md_path
    from ydbdoc_review.validation.fragment_repair import (
        _page_declares_fragment,
        _resolve_href_path,
        add_explicit_ascii_fragment_anchor,
        declare_explicit_fragment_on_include_owner,
    )
    from ydbdoc_review.validation.href_parity import _iter_visible_md_link_matches

    proposed_text: dict[str, str] = {}
    before_text: dict[str, str] = {}
    ru_owner_by_en: dict[str, str] = {}
    fragments_by_en: dict[str, list[str]] = {}

    def read_with_proposals(path: str) -> str | None:
        normalized = path.replace("\\", "/")
        if normalized in proposed_text:
            return proposed_text[normalized]
        return read_candidate(normalized)

    def canonical_ru(path: str) -> str:
        return follow_redirect_repo_md_path(
            path.replace("\\", "/"),
            redirects_yaml or "",
            docs_root=docs_root,
        )

    def ru_carrier_has_same_edge(
        en_page: str,
        en_target: str,
        fragment: str,
    ) -> bool:
        ru_page = counterpart(en_page, docs_root)
        expected_ru_target = counterpart(en_target, docs_root)
        if ru_page is None or expected_ru_target is None:
            return False
        ru_page_text = read_candidate(ru_page)
        if not ru_page_text:
            return False
        expected_ru_target = canonical_ru(expected_ru_target)
        for ru_match in _iter_visible_md_link_matches(ru_page_text):
            ru_href = ru_match.group(2).strip().split(maxsplit=1)[0]
            if "#" not in ru_href:
                continue
            ru_href_path, ru_fragment = ru_href.rsplit("#", 1)
            if (
                not ru_href_path.endswith(".md")
                or not ru_fragment
                or unquote(ru_fragment) != unquote(fragment)
            ):
                continue
            ru_target = _resolve_href_path(ru_page, ru_href_path)
            if ru_target is not None and canonical_ru(ru_target) == expected_ru_target:
                return True
        return False

    for page in dict.fromkeys(path.replace("\\", "/") for path in paths):
        if not page.endswith(".md") or "/docs/en/" not in page.replace("\\", "/"):
            continue
        page_text = read_page(page)
        if not page_text:
            continue
        for match in _iter_visible_md_link_matches(page_text):
            href = match.group(2).strip().split(maxsplit=1)[0]
            if "#" not in href:
                continue
            href_path, frag = href.rsplit("#", 1)
            if not href_path.endswith(".md") or not frag or not frag.isascii():
                continue
            en_wrapper = _resolve_href_path(page, href_path)
            if en_wrapper is None:
                continue
            ru_wrapper = counterpart(en_wrapper, docs_root)
            if ru_wrapper is None:
                continue
            ru_wrapper = canonical_ru(ru_wrapper)
            canonical_en_wrapper = counterpart(ru_wrapper, docs_root)
            if canonical_en_wrapper is None:
                continue
            en_wrapper = canonical_en_wrapper
            if not ru_carrier_has_same_edge(page, en_wrapper, frag):
                continue
            en_text, ru_text = read_with_proposals(en_wrapper), read_with_proposals(
                ru_wrapper
            )
            if en_text is None or ru_text is None:
                continue
            if _page_declares_fragment(en_text, frag):
                continue
            en_includes = collect_yfm_includes(en_text)
            ru_includes = collect_yfm_includes(ru_text)
            owners: list[tuple[str, str, str, str]] = []
            if _page_declares_fragment(ru_text, frag):
                owners.append((en_wrapper, en_text, ru_wrapper, ru_text))
            for index, ru_inc in enumerate(ru_includes):
                if index >= len(en_includes):
                    continue
                ru_owner = resolve_locale_md_path(
                    ru_wrapper, ru_inc.path, docs_root=docs_root
                )
                en_owner = resolve_locale_md_path(
                    en_wrapper, en_includes[index].path, docs_root=docs_root
                )
                if ru_owner:
                    ru_owner = canonical_ru(ru_owner)
                if en_owner:
                    canonical_owner_ru = counterpart(en_owner, docs_root)
                    if canonical_owner_ru:
                        en_owner = counterpart(
                            canonical_ru(canonical_owner_ru), docs_root
                        )
                ru_owner_text = read_with_proposals(ru_owner) if ru_owner else None
                en_owner_text = read_with_proposals(en_owner) if en_owner else None
                if (
                    ru_owner
                    and en_owner
                    and ru_owner_text
                    and en_owner_text is not None
                    and counterpart(ru_owner, docs_root) == en_owner
                    and _page_declares_fragment(ru_owner_text, frag)
                    and not collect_yfm_includes(ru_owner_text)
                ):
                    owners.append((en_owner, en_owner_text, ru_owner, ru_owner_text))
            if len(owners) != 1:
                if ambiguous_out is not None:
                    for en_owner, _en_text, ru_owner, _ru_text in dict.fromkeys(
                        (owner[0], owner[1], owner[2], owner[3]) for owner in owners
                    ):
                        ambiguous_out.append(
                            _AmbiguousExactFragmentOwner(
                                ru_owner_path=ru_owner,
                                en_owner_path=en_owner,
                                reason=(
                                    "exact fragment owner is not uniquely aligned "
                                    f"for {page}#{frag}"
                                ),
                            )
                        )
                continue
            en_owner, en_owner_text, ru_owner, ru_owner_text = owners[0]
            fixed = add_explicit_ascii_fragment_anchor(en_owner_text, ru_owner_text, frag)
            if fixed is None and "/_includes/" in en_owner.replace("\\", "/"):
                fixed = declare_explicit_fragment_on_include_owner(
                    en_owner_text, ru_owner_text, frag
                )
            if fixed is None or fixed == en_owner_text:
                if fixed is None and ambiguous_out is not None:
                    ambiguous_out.append(
                        _AmbiguousExactFragmentOwner(
                            ru_owner_path=ru_owner,
                            en_owner_path=en_owner,
                            reason=f"exact declaration repair is ambiguous for {page}#{frag}",
                        )
                    )
                continue
            before_text.setdefault(en_owner, en_owner_text)
            proposed_text[en_owner] = fixed
            ru_owner_by_en[en_owner] = ru_owner
            fragments_by_en.setdefault(en_owner, []).append(frag)

    return tuple(
        _ExactFragmentDeclarationProposal(
            ru_owner_path=ru_owner_by_en[en_owner],
            en_owner_path=en_owner,
            before_en_text=before_text[en_owner],
            after_en_text=proposed_text[en_owner],
            fragments=tuple(dict.fromkeys(fragments_by_en[en_owner])),
        )
        for en_owner in proposed_text
    )


def _declare_exact_ascii_fragment_targets_after_apply(
    repo_path: str,
    paths: list[str],
    *,
    dry_run: bool,
    merge_base_with: str | None = None,
    ru_content_ref: str | None = None,
    budget: MarkdownDependencyBudget | None = None,
    docs_root: str = "ydb/docs",
) -> list[str]:
    """Admit and apply proven declarations on unique aligned RU owners."""
    overlay = {p.replace("\\", "/") for p in paths}
    if merge_base_with:
        read_en_candidate = _final_tree_reader(repo_path, merge_base_with, overlay)

        def read_candidate(path: str) -> str | None:
            normalized = path.replace("\\", "/")
            if ru_content_ref and "/docs/ru/" in normalized:
                return read_text_at_commit(repo_path, ru_content_ref, normalized)
            return read_en_candidate(normalized)
    else:

        def read_candidate(path: str) -> str | None:
            return read_text(repo_path, path)

    proposals = _discover_exact_ascii_fragment_declaration_proposals(
        paths,
        read_page=lambda path: read_text(repo_path, path),
        read_candidate=read_candidate,
        docs_root=docs_root,
        redirects_yaml=read_candidate(f"{docs_root.strip('/')}/redirects.yaml"),
    )
    admission = budget or MarkdownDependencyBudget()
    declared: list[str] = []
    for proposal in proposals:
        if not admission.admit(
            proposal.ru_owner_path,
            warning_path=proposal.en_owner_path,
        ):
            continue
        declared.append(proposal.en_owner_path)
        if not dry_run:
            write_text(repo_path, proposal.en_owner_path, proposal.after_en_text)
    return declared


def _reconstruct_late_dependency_budget_for_verify(
    repo_path: str,
    scope_plan: TranslationScopePlan,
    provenance: TranslationArtifactProvenance,
    *,
    verified_commit_sha: str,
    docs_root: str,
) -> frozenset[str]:
    """Recover producer-late admissions from immutable A05 artifact evidence.

    Planning has already reconstructed the ordinary budget from H0/R against B.
    P is used only for the exact root-artifact P->C delta.  The current verified
    commit K is consulted only when discovering still-unresolved proposals.
    """
    artifact_sha = provenance.candidate_sha
    prepared_parent_sha = commit_parent_sha(repo_path, artifact_sha)
    ru_sha = provenance.authority.ru_sha
    artifact_changes = first_parent_commit_changes(repo_path, artifact_sha)
    artifact_changed_en = {
        path.replace("\\", "/")
        for path, kind in artifact_changes
        if kind != "deleted"
        and path.replace("\\", "/").startswith(f"{docs_root.strip('/')}/en/")
        and path.endswith(".md")
    }
    authorized_en_pages = tuple(
        sorted(
            en_path
            for ru_path in scope_plan.doc_ru_paths
            if ru_path not in scope_plan.doc_deleted
            and (en_path := counterpart(ru_path, docs_root)) is not None
            and en_path.endswith(".md")
        )
    )
    planned_overlay_paths = frozenset(authorized_en_pages)

    def read_prepared_candidate(path: str) -> str | None:
        normalized = path.replace("\\", "/")
        if normalized.startswith(f"{docs_root.strip('/')}/ru/"):
            return read_text_at_commit(repo_path, ru_sha, normalized)
        if normalized in planned_overlay_paths:
            return read_text_at_commit(repo_path, artifact_sha, normalized)
        return read_text_at_commit(repo_path, prepared_parent_sha, normalized)

    ambiguous: list[_AmbiguousExactFragmentOwner] = []
    artifact_proposals = _discover_exact_ascii_fragment_declaration_proposals(
        authorized_en_pages,
        read_page=lambda path: read_text_at_commit(repo_path, artifact_sha, path),
        read_candidate=read_prepared_candidate,
        docs_root=docs_root,
        redirects_yaml=(
            read_text_at_commit(
                repo_path,
                artifact_sha,
                f"{docs_root.strip('/')}/redirects.yaml",
            )
            or read_text_at_commit(
                repo_path,
                prepared_parent_sha,
                f"{docs_root.strip('/')}/redirects.yaml",
            )
        ),
        ambiguous_out=ambiguous,
    )
    budget = scope_plan.dependency_budget
    proven_owner_paths: set[str] = set()
    recovered_en_paths: set[str] = set()
    for proposal in artifact_proposals:
        if proposal.en_owner_path not in artifact_changed_en:
            continue
        artifact_owner_text = read_text_at_commit(
            repo_path, artifact_sha, proposal.en_owner_path
        )
        if artifact_owner_text != proposal.after_en_text:
            budget.mark_uncertain(
                proposal.ru_owner_path,
                reason="root artifact owner bytes do not equal the exact expected repair",
            )
            recovered_en_paths.add(proposal.en_owner_path)
            continue
        if budget.replay_proven(proposal.ru_owner_path):
            proven_owner_paths.add(proposal.ru_owner_path)
            recovered_en_paths.add(proposal.en_owner_path)
        else:
            budget.mark_uncertain(
                proposal.ru_owner_path,
                reason="root artifact proves a late repair but its admission is ambiguous",
            )
            recovered_en_paths.add(proposal.en_owner_path)

    for candidate in sorted(
        ambiguous,
        key=lambda value: (value.ru_owner_path, value.en_owner_path, value.reason),
    ):
        if (
            candidate.en_owner_path not in artifact_changed_en
            or candidate.ru_owner_path in proven_owner_paths
        ):
            continue
        parent_text = read_text_at_commit(
            repo_path, prepared_parent_sha, candidate.en_owner_path
        )
        artifact_text = read_text_at_commit(
            repo_path, artifact_sha, candidate.en_owner_path
        )
        if parent_text == artifact_text:
            continue
        budget.mark_uncertain(candidate.ru_owner_path, reason=candidate.reason)
        recovered_en_paths.add(candidate.en_owner_path)

    def read_verified_candidate(path: str) -> str | None:
        normalized = path.replace("\\", "/")
        if normalized.startswith(f"{docs_root.strip('/')}/ru/"):
            return read_text_at_commit(repo_path, ru_sha, normalized)
        return read_text_at_commit(repo_path, verified_commit_sha, normalized)

    remaining = _discover_exact_ascii_fragment_declaration_proposals(
        authorized_en_pages,
        read_page=lambda path: read_text_at_commit(
            repo_path, verified_commit_sha, path
        ),
        read_candidate=read_verified_candidate,
        docs_root=docs_root,
        redirects_yaml=read_text_at_commit(
            repo_path,
            verified_commit_sha,
            f"{docs_root.strip('/')}/redirects.yaml",
        ),
    )
    for proposal in remaining:
        budget.admit(
            proposal.ru_owner_path,
            warning_path=proposal.en_owner_path,
        )
    return frozenset(recovered_en_paths)


def _run_verify_pairs(
    contents: list[PairContent],
    client: YandexLLMClient,
    glossary: Glossary,
    config: Config,
    *,
    en_toc_reachable: frozenset[str] | None = None,
    docs_text_reader=None,
    docs_repo_path: str | None = None,
) -> PRTranslationResult:
    """Critic-only QA for existing RU/EN pairs."""
    state = PRRunState(contents=contents)
    ctx = PRHarnessContext.from_options(
        client,
        glossary=glossary,
        config=config,
        en_toc_reachable=en_toc_reachable,
        docs_text_reader=docs_text_reader,
        docs_repo_path=docs_repo_path,
    )
    return PRHarness(VERIFY_PR_PROFILE).run(state, ctx)


def job_requires_nonzero_exit(job: DocJobResult, *, no_commit: bool = False) -> bool:
    """§11: do not exit success when blockers skipped commit/push/PR creation."""
    if job.dry_run or no_commit:
        return False
    if job.blocked:
        return True
    if job.mode == "doc_verify":
        return _pr_result_has_blockers(job.pr_result)
    if job.mode not in ("doc_translate", "doc_continue"):
        return False
    if job.translation_pr_number is not None:
        return False
    return not _allowed_success_without_translation_pr(job)


def _allowed_success_without_translation_pr(job: DocJobResult) -> bool:
    """Bilingual no-op / empty scope: publish was not required (§11 / P7)."""
    if job.blocked or _pr_result_has_blockers(job.pr_result):
        return False
    pairs = job.pr_result.pair_results
    nav = job.pr_result.navigation_results
    if not pairs and not nav:
        return True
    if pairs and all(p.plan.action == "skip" for p in pairs) and not nav:
        return True
    return False


def _pr_result_has_blockers(result: PRTranslationResult) -> bool:
    return result_has_blocking_findings(result)


def _publication_withheld(result: PRTranslationResult) -> bool:
    return result.publication_impact in {
        PublicationImpact.WITHHOLD_INCOMPLETE,
        PublicationImpact.WITHHOLD_UNSAFE,
    }


def _translation_checkpoint_fingerprint(
    config: Config,
    glossary: Glossary,
    client: YandexLLMClient,
    *,
    effective_continue_feedback: str | None,
) -> str:
    """Hash every nonsecret input that can alter a translated unit."""
    prompt_names = (
        "system_common",
        "system_glossary",
        "translate",
        "translate_glossary",
        "en_style_guide",
        "repair",
        "critic_feedback_repair",
    )
    payload = {
        "continue_feedback_sha256": hashlib.sha256(
            (effective_continue_feedback or "").encode("utf-8")
        ).hexdigest(),
        "glossary_sha256": hashlib.sha256(
            str(glossary.to_prompt_yaml()).encode("utf-8")
        ).hexdigest(),
        "llm": config.llm.model_dump(mode="json"),
        "models": list(client.model_chain_for_role("translate")),
        "prompt_templates": {
            name: hashlib.sha256(
                load_template(name, version=config.prompts.version).encode("utf-8")
            ).hexdigest()
            for name in prompt_names
        },
        "prompt_version": config.prompts.version,
        "schemas": {
            "checkpoint": "translation/v1",
            "protection": "inline-ast-v1",
            "renderer": "markdown-ir-v1",
            "segmentation": "segment-v1",
        },
        "translation": config.translation.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_translation_resume_parent(
    *,
    ops_ctx: object | None,
    checkpoint: CheckpointWriter | None,
    explicit_parent_run_id: str | None,
) -> str | None:
    """Resolve one eligible parent without inheriting any candidate verdict."""
    if checkpoint is None:
        if explicit_parent_run_id:
            logger.warning(
                "Translation resume parent=%s rejected: no trusted checkpoint store",
                explicit_parent_run_id,
            )
        return None
    if ops_ctx is None:
        if explicit_parent_run_id:
            logger.warning(
                "Translation resume parent=%s rejected: run metadata unavailable",
                explicit_parent_run_id,
            )
        return None
    ledger = getattr(ops_ctx, "ledger", None)
    repo = getattr(ops_ctx, "repo", None)
    source_pr = getattr(ops_ctx, "source_pr", None)
    current_run_id = getattr(ops_ctx, "run_id", None)
    if ledger is None or not isinstance(repo, str) or type(source_pr) is not int:
        logger.warning("Translation resume disabled: incomplete current run metadata")
        return None

    excluded: list[str] = []
    if isinstance(current_run_id, str) and current_run_id:
        excluded.append(current_run_id)
    seen: set[str] = set(excluded)

    if explicit_parent_run_id is not None:
        try:
            candidate = ledger.latest_run_id(
                source_pr,
                modes=("translate", "continue"),
                statuses=("ok", "published_red", "failed"),
                repo=repo,
                exclude_run_ids=tuple(excluded),
                run_id=explicit_parent_run_id,
            )
        except Exception as exc:
            logger.warning("Translation resume ledger lookup failed: %s", exc)
            return None
        if candidate != explicit_parent_run_id:
            logger.warning(
                "Translation resume parent=%s rejected: no matching completed "
                "same-repository run metadata",
                explicit_parent_run_id,
            )
            return None
        return (
            candidate
            if run_has_usable_verified_units(
                checkpoint.store,
                candidate,
                checkpoint.identity,
            )
            else None
        )

    while True:
        try:
            candidate = ledger.latest_run_id(
                source_pr,
                modes=("translate", "continue"),
                statuses=("ok", "published_red", "failed"),
                repo=repo,
                exclude_run_ids=tuple(excluded),
            )
        except Exception as exc:
            logger.warning("Translation resume ledger lookup failed: %s", exc)
            return None
        if candidate is None:
            return None
        if candidate in seen:
            logger.warning(
                "Translation resume ledger made no exclusion progress at run=%s",
                candidate,
            )
            return None
        seen.add(candidate)

        usable = run_has_usable_verified_units(
            checkpoint.store,
            candidate,
            checkpoint.identity,
        )
        if usable:
            return candidate
        excluded.append(candidate)


def _attach_source_coverage_plans(
    contents: list[PairContent],
    *,
    scope_plan: TranslationScopePlan,
    authority: RuAuthority,
    checkpoint: CheckpointWriter | None,
    resume_parent_run_id: str | None,
) -> list[PairContent]:
    """Plan each RU target from frozen bytes and exact Task 5 receipts only."""
    planned: list[PairContent] = []
    for content in contents:
        pair = content.pair
        source_text = content.ru_text
        if source_text is None or pair.ru_deleted or pair.en_changed:
            planned.append(content)
            continue

        verified_units = []
        if checkpoint is not None and resume_parent_run_id is not None:
            try:
                segments = extract_segments(parse_markdown(source_text))
            except (AssertionError, TypeError, ValueError):
                segments = []
            for segment in segments:
                key = translation_unit_key_for_segment(
                    segment,
                    source_path=pair.ru_path,
                    target_locale="en",
                )
                verified = load_verified_unit(
                    checkpoint.store,
                    resume_parent_run_id,
                    checkpoint.identity,
                    key,
                    segment.text.encode("utf-8"),
                )
                if verified is not None:
                    verified_units.append(verified)

        coverage_plan = plan_source_coverage(
            source_path=pair.ru_path,
            source_text=source_text,
            existing_en=content.en_text,
            authority=authority,
            required_fragments=scope_plan.required_fragments_for(pair.ru_path),
            verified_units=tuple(verified_units),
            checkpoint_identity=(checkpoint.identity if checkpoint is not None else None),
        )
        planned.append(replace(content, coverage_plan=coverage_plan))
    return planned


def _persist_candidate_coverage_evidence(
    *,
    repo_path: str,
    candidate_sha: str,
    authority: RuAuthority,
    contents: list[PairContent],
    store: TranscriptStore,
    run_id: str,
) -> CoverageEvidence | None:
    planned = {
        content.pair.en_path: content.coverage_plan
        for content in contents
        if content.coverage_plan is not None
    }
    if not any(plan.mode == "units" for plan in planned.values()):
        return None
    baseline_en = {
        content.pair.en_path: content.en_text
        for content in contents
        if content.pair.en_path in planned
    }
    candidate_files: dict[str, str] = {}
    for path in planned:
        text = read_text_at_commit(repo_path, candidate_sha, path)
        if text is None:
            raise TranslationCheckpointError(
                f"coverage evidence candidate file is missing: {path}"
            )
        candidate_files[path] = text
    try:
        evidence = build_coverage_evidence(
            authority=authority,
            candidate_sha=candidate_sha,
            plans=planned,  # type: ignore[arg-type]
            baseline_en=baseline_en,
            candidate_files=candidate_files,
        )
        save_coverage_evidence(store, run_id, evidence)
    except ValueError as exc:
        raise TranslationCheckpointError(str(exc)) from exc
    return evidence


def _persist_repaired_coverage_evidence(
    *,
    repo_path: str,
    candidate_sha: str,
    prior: CoverageEvidence,
    store: TranscriptStore,
    run_id: str,
) -> CoverageEvidence:
    plans = dict(prior.plans)
    baseline_en = {
        path: read_text_at_commit(repo_path, prior.authority.baseline_sha, path)
        for path in plans
    }
    candidate_files: dict[str, str] = {}
    for path in plans:
        text = read_text_at_commit(repo_path, candidate_sha, path)
        if text is None:
            raise TranslationCheckpointError(
                f"coverage evidence repaired candidate file is missing: {path}"
            )
        candidate_files[path] = text
    try:
        evidence = build_coverage_evidence(
            authority=prior.authority,
            candidate_sha=candidate_sha,
            plans=plans,
            baseline_en=baseline_en,
            candidate_files=candidate_files,
        )
        save_coverage_evidence(store, run_id, evidence)
    except ValueError as exc:
        raise TranslationCheckpointError(str(exc)) from exc
    return evidence


def _translation_checkpoint_scope(
    plan: TranslationScopePlan,
) -> dict[str, tuple[str, ...]]:
    memberships = (
        ("doc_from_diff", plan.doc_from_diff),
        ("doc_from_main", plan.doc_from_main),
        ("nav_from_diff", plan.nav_from_diff),
        ("nav_from_main", plan.nav_from_main),
        ("doc_deleted", plan.doc_deleted),
    )
    return {
        path: tuple(name for name, paths in memberships if path in paths)
        for path in sorted(plan.all_ru_paths | plan.doc_deleted)
    }


def _translation_checkpoint_blockers(
    result: PRTranslationResult,
) -> tuple[str, ...]:
    blockers: list[str] = list(result.completeness_gaps)
    blockers.extend(blocker.message for blocker in result.final_tree_blockers)
    for pair in result.pair_results:
        if pair.error:
            blockers.append(pair.error)
        if pair.soft_keep_reason:
            blockers.append(f"translation_soft_keep: {pair.soft_keep_reason}")
        if pair.file_result is not None:
            blockers.extend(pair.file_result.heuristic_blocking)
            if pair.file_result.segment_alignment_error:
                blockers.append(pair.file_result.segment_alignment_error)
            blockers.extend(
                action.message for action in pair.file_result.manual_actions
            )
        blockers.extend(issue.message for issue in pair.validation_issues)
    for navigation in result.navigation_results:
        if navigation.error:
            blockers.append(navigation.error)
        if navigation.verdict == "blocked":
            blockers.extend(navigation.warnings)
    return tuple(dict.fromkeys(blockers))


def _finish_translation_checkpoint(
    checkpoint: CheckpointWriter,
    result: PRTranslationResult,
    scope_plan: TranslationScopePlan,
) -> None:
    """Retain navigation and the final pre-publication candidate manifest."""
    for navigation in result.navigation_results:
        nav_blockers: list[str] = []
        if navigation.error:
            nav_blockers.append(navigation.error)
        if navigation.verdict == "blocked":
            nav_blockers.extend(navigation.warnings)
        checkpoint.save_navigation(
            navigation.en_path,
            (
                navigation.target_text.encode("utf-8")
                if navigation.target_text is not None
                else None
            ),
            blockers=tuple(dict.fromkeys(nav_blockers)),
        )
    checkpoint.finish(
        status=result.publication_impact.value,
        blockers=_translation_checkpoint_blockers(result),
        scope=_translation_checkpoint_scope(scope_plan),
    )


def _persist_continuability(
    repo_path: str,
    *,
    source_pr: int,
    fixed_shas: dict[str, str],
    translation_pr: int | None,
    unfinished: bool,
    unfinished_stage: str = "verify",
    ops_ctx: object | None = None,
) -> str | None:
    """Write continuability artifact; return relative path when written."""
    if not fixed_shas or source_pr <= 0:
        return None
    if unfinished:
        path = mark_continuable(
            repo_path,
            source_pr=source_pr,
            unfinished_stage=unfinished_stage,
            fixed_shas=fixed_shas,
            translation_pr=translation_pr,
        )
    else:
        path = clear_continuability(repo_path, source_pr)
    if path is None:
        return None
    rel = relative_state_path(source_pr)
    if ops_ctx is not None:
        store = getattr(ops_ctx, "store", None)
        run_id = getattr(ops_ctx, "run_id", None)
        if store is not None and run_id:
            state = load_continuability(repo_path, source_pr)
            if state is not None:
                try:
                    store.put(run_id, CONTINUABILITY_STORE_KEY, dump_continuability_json(state))
                except Exception as exc:
                    logger.warning("Failed to store continuability in transcript: %s", exc)
    return rel


def _load_continuability_for_continue(
    repo_path: str,
    source_pr: int,
    *,
    ops_store: object | None = None,
    parent_run_id: str | None = None,
) -> ContinuabilityState | None:
    state = load_continuability(repo_path, source_pr)
    # A local artifact is an explicit decision, including a terminal denial.
    # Only a genuinely fresh checkout may fall back to the trusted parent run.
    if state is not None:
        return state
    if ops_store is not None and parent_run_id:
        try:
            raw = ops_store.get(parent_run_id, CONTINUABILITY_STORE_KEY)
        except Exception as exc:
            logger.warning("Failed to load continuability from parent run: %s", exc)
            raw = None
        stored = load_continuability_from_bytes(raw)
        if stored is not None and stored.allows_continue():
            return stored
    return state


def _collect_fixed_shas(
    repo_path: str,
    *,
    merge_base_with: str,
    ru_ref: str | None,
    head_sha: str | None,
) -> dict[str, str]:
    """SHAs frozen for this job (must be non-empty before continuability)."""
    shas: dict[str, str] = {}
    if head_sha:
        shas["head"] = head_sha
    if ru_ref:
        shas["ru_ref"] = ru_ref
    shas["merge_base"] = resolve_commit_ref(repo_path, merge_base_with)
    return shas


def run_doc_translate(
    *,
    repo_path: str,
    github_repo: str,
    pr_number: int,
    merge_base_with: str = "origin/main",
    dry_run: bool = False,
    no_commit: bool = False,
    config: Config | None = None,
    continue_feedback: str | None = None,
    ops_mode: str = "translate",
    parent_run_id: str | None = None,
    checkpoint: CheckpointWriter | None = None,
    _ops_ctx: OpsContext | None = None,
) -> DocJobResult:
    """Full ``doc_translate`` workflow for a source PR."""
    started = time.monotonic()
    cfg = config or load_config()
    api_token, push_token = _github_tokens(cfg)
    owner, repo = parse_repo(github_repo)
    gh = GitHubClient(api_token)

    ops_ctx = _ops_ctx
    if ops_ctx is None:
        ops_ctx, gate, deny_body = begin_ops_job(
            mode=ops_mode,
            repo=github_repo,
            source_pr=pr_number,
            continue_feedback=continue_feedback,
            parent_run_id=parent_run_id,
        )
        if not gate.ok:
            if deny_body and not dry_run:
                _safe_post_issue_comment(gh, owner, repo, pr_number, deny_body, label="ops deny")
            return DocJobResult(
                mode=f"doc_{ops_mode}",
                pr_number=pr_number,
                source_pr_number=pr_number,
                dry_run=dry_run,
                blocked=True,
            )
    elif (
        getattr(ops_ctx, "mode", None) != ops_mode
        or getattr(ops_ctx, "repo", None) != github_repo
        or getattr(ops_ctx, "source_pr", None) != pr_number
    ):
        raise RuntimeError("pre-authorized ops context does not match translation job")
    if ops_mode == "continue":
        continuability = _load_continuability_for_continue(
            repo_path,
            pr_number,
            ops_store=getattr(ops_ctx, "store", None),
            parent_run_id=getattr(ops_ctx, "parent_run_id", None),
        )
        if (
            continuability is None
            or not continuability.allows_continue()
            or continuability.source_pr != pr_number
        ):
            if ops_ctx is not None and not dry_run:
                finish_ops_job(ops_ctx, status="failed", cost_rub=0.0)
            return DocJobResult(
                mode="doc_continue",
                pr_number=pr_number,
                source_pr_number=pr_number,
                dry_run=dry_run,
                blocked=True,
            )

    merge_base_with = resolve_commit_ref(repo_path, merge_base_with)
    source_checkout_sha = resolve_commit_ref(repo_path, "HEAD")

    effective_continue_feedback = continue_feedback or (
        ops_ctx.continue_feedback if ops_ctx else None
    )
    if ops_mode == "continue" and ops_ctx is not None:
        effective_continue_feedback = compose_continue_feedback(
            effective_continue_feedback,
            load_parent_run_context(ops_ctx),
        )

    ctx = pull_request_context(gh, owner, repo, pr_number)
    if ctx.state != "open" and not ctx.merged:
        if ops_ctx is not None and not dry_run:
            finish_ops_job(ops_ctx, status="failed", cost_rub=0.0)
        if not dry_run:
            _safe_post_issue_comment(
                gh,
                owner,
                repo,
                pr_number,
                "Метка `doc_translate` обрабатывает только открытые и уже слитые PR.",
                label="doc_translate blocked",
            )
        return DocJobResult(
            mode="doc_translate" if ops_mode == "translate" else f"doc_{ops_mode}",
            pr_number=pr_number,
            source_pr_number=pr_number,
            dry_run=dry_run,
            blocked=True,
        )

    effective_authority_mode = (
        RuAuthorityMode.SOURCE_PRESERVING
        if "doc_translate_source_preserving" in ctx.labels
        else cfg.translation.ru_authority_mode
    )
    authority_selection = freeze_ru_authority(
        repo_path,
        source_repo=github_repo,
        source_pr=pr_number,
        source_head_sha=ctx.head_sha,
        source_base_sha=ctx.base_sha,
        merge_commit_sha=ctx.merge_commit_sha,
        merged=ctx.merged,
        fork=is_fork_head(ctx),
        baseline_sha=merge_base_with,
        checkout_sha=source_checkout_sha,
        mode=effective_authority_mode,
    )
    authority = authority_selection.authority
    branch = f"{cfg.paths.translation_branch_prefix}{pr_number}"
    destination_lease = _snapshot_destination_lease(gh, owner, repo, branch)
    upstream_url = repo_https_clone_url(owner, repo)
    branch_remote_url, branch_start_ref = translation_branch_base(ctx)
    translation_prepare_parent_sha = authority_selection.prepare_parent_sha
    ru_ref = authority.ru_sha
    ru_base_ref = authority.ru_base_sha
    fixed_shas = _collect_fixed_shas(
        repo_path,
        merge_base_with=merge_base_with,
        ru_ref=ru_ref,
        head_sha=ctx.head_sha,
    )
    if not dry_run and not no_commit:
        # A new attempt starts terminal. Admission is granted only after a
        # concrete translation PR exists and inline verification is pending.
        _persist_continuability(
            repo_path,
            source_pr=pr_number,
            fixed_shas=fixed_shas,
            translation_pr=None,
            unfinished=False,
            ops_ctx=ops_ctx,
        )

    changes = list_pr_file_changes_api(gh, owner, repo, pr_number)
    source_api_paths = frozenset(path for path, _kind in changes)
    changes = filter_translate_changes(changes, cfg.paths.translate_skip_globs)
    docs_root = cfg.paths.docs_root
    read_ru, read_en_base, read_ru_base = make_repo_scope_readers(
        repo_path,
        merge_base_with,
        ru_content_ref=ru_ref,
        ru_base_ref=ru_base_ref,
        authority=authority,
    )
    scope_plan = plan_translation_scope(
        changes,
        read_ru=read_ru,
        read_en_base=read_en_base,
        read_ru_base=read_ru_base,
        docs_root=docs_root,
    )
    skip_globs = cfg.paths.translate_skip_globs
    if skip_globs:
        scope_plan = replace(
            scope_plan,
            doc_ru_paths=filter_path_set(scope_plan.doc_ru_paths, skip_globs),
            doc_from_diff=filter_path_set(scope_plan.doc_from_diff, skip_globs),
            doc_from_main=filter_path_set(scope_plan.doc_from_main, skip_globs),
            nav_ru_paths=filter_path_set(scope_plan.nav_ru_paths, skip_globs),
            nav_from_diff=filter_path_set(scope_plan.nav_from_diff, skip_globs),
            nav_from_main=filter_path_set(scope_plan.nav_from_main, skip_globs),
            doc_deleted=filter_path_set(scope_plan.doc_deleted, skip_globs),
        )
    logger.info(
        "Scope plan for PR #%s: %s doc paths (%s diff + %s main), %s nav paths",
        pr_number,
        len(scope_plan.doc_ru_paths),
        len(scope_plan.doc_from_diff),
        len(scope_plan.doc_from_main),
        len(scope_plan.nav_ru_paths),
    )
    bilingual_skip = frozenset(bilingual_en_mirrors(changes, docs_root=docs_root))
    pairs = doc_pairs_from_plan(
        scope_plan,
        docs_root=docs_root,
        skip_en_paths=bilingual_skip,
    )
    nav_pairs = merge_navigation_pair_lists(
        navigation_pairs_from_plan(scope_plan, docs_root=docs_root),
        build_navigation_pairs(changes, docs_root=docs_root),
    )
    # Redirect retargeting is authorized only for EN mirrors of files changed
    # by the source PR, never synthetic dependency pages added by scope closure.
    redirect_impact_scope = frozenset(
        en_path
        for path, kind in changes
        if kind != "deleted"
        and (en_path := counterpart(path, docs_root)) is not None
        and en_path.endswith(".md")
    )
    changes = merge_pr_file_changes(changes, synthetic_changes_from_plan(scope_plan))
    job = DocJobResult(
        mode="doc_translate" if ops_mode == "translate" else f"doc_{ops_mode}",
        pr_number=pr_number,
        source_pr_number=pr_number,
        translation_branch=branch,
        dry_run=dry_run,
    )
    executable_ru_paths = frozenset(pair.ru_path for pair in pairs)
    preflight_plan = replace(
        scope_plan,
        doc_ru_paths=frozenset(scope_plan.doc_ru_paths & executable_ru_paths),
        doc_from_diff=frozenset(scope_plan.doc_from_diff & executable_ru_paths),
        doc_from_main=frozenset(scope_plan.doc_from_main & executable_ru_paths),
        doc_deleted=frozenset(scope_plan.doc_deleted & executable_ru_paths),
    )
    preflight = preflight_translation(
        preflight_plan,
        read_ru=read_ru,
        read_ru_base=read_ru_base,
        read_en_base=read_en_base,
        docs_root=docs_root,
    )
    if preflight.blockers:
        logger.error(
            "Translation preflight withheld PR #%s before model work: %s",
            pr_number,
            preflight.blockers,
        )
        pr_result = PRTranslationResult(
            completeness_gaps=list(preflight.blockers),
        )
        _merge_yellow_warnings(pr_result, scope_plan.link_dep_warnings)
        refresh_publication_impact(pr_result)
        job.pr_result = pr_result
        if not dry_run:
            elapsed = time.monotonic() - started
            meta = ReportMeta(mode="doc_translate", report_number=1, elapsed_s=elapsed)
            job.source_comment_url = _safe_post_issue_comment(
                gh,
                owner,
                repo,
                pr_number,
                append_retention_footer(
                    build_source_pr_comment(
                        pr_result,
                        translation_pr_number=None,
                        meta=meta,
                        config=cfg,
                        committed=False,
                    )
                ),
                label="source PR summary",
            )
        if ops_ctx is not None:
            finish_ops_job(ops_ctx, status="failed", cost_rub=0.0)
        return job
    if not pairs and not nav_pairs:
        logger.info("No doc or navigation pairs in PR #%s", pr_number)
        # Bilingual RU+EN in the same source PR are dropped from ``pairs`` via
        # ``skip_en_paths`` before analyze — still post «перевод не требуется»
        # (§6.76 / #48751). Without this early path the comment never appeared.
        pr_result = _pr_result_for_bilingual_skips(bilingual_skip, docs_root=docs_root)
        _merge_yellow_warnings(pr_result, scope_plan.link_dep_warnings)
        job.pr_result = pr_result
        if pr_result.pair_results and not dry_run:
            elapsed = time.monotonic() - started
            meta = ReportMeta(mode="doc_translate", report_number=1, elapsed_s=elapsed)
            job.source_comment_url = _safe_post_issue_comment(
                gh,
                owner,
                repo,
                pr_number,
                append_retention_footer(
                    build_source_pr_comment(
                        pr_result,
                        translation_pr_number=None,
                        meta=meta,
                        config=cfg,
                        committed=False,
                    )
                ),
                label="source PR summary",
            )
        if ops_ctx is not None:
            finish_ops_job(ops_ctx, status="ok", cost_rub=0.0)
        return job

    client = create_llm_client(cfg)
    if ops_ctx is not None:
        client.transcript_recorder = ops_ctx.recorder
    glossary = load_glossary()
    current_identity = CheckpointIdentity(
        authority,
        _translation_checkpoint_fingerprint(
            cfg,
            glossary,
            client,
            effective_continue_feedback=effective_continue_feedback,
        ),
    )
    active_checkpoint = checkpoint
    if active_checkpoint is None and ops_ctx is not None:
        store = getattr(ops_ctx, "store", None)
        run_id = getattr(ops_ctx, "run_id", None)
        if (
            store is not None
            and run_id
            and not isinstance(store, NullTranscriptStore)
        ):
            active_checkpoint = CheckpointWriter(
                store,
                run_id,
                current_identity,
            )
    if active_checkpoint is not None:
        if active_checkpoint.identity.authority != current_identity.authority:
            raise TranslationCheckpointError(
                "translation checkpoint authority mismatch for current workflow"
            )
        if active_checkpoint.identity != current_identity:
            raise TranslationCheckpointError(
                "translation checkpoint identity mismatch for current workflow"
            )
    requested_resume_parent = parent_run_id or (
        getattr(ops_ctx, "parent_run_id", None) if ops_ctx is not None else None
    )
    resume_parent_run_id = _resolve_translation_resume_parent(
        ops_ctx=ops_ctx,
        checkpoint=active_checkpoint,
        explicit_parent_run_id=requested_resume_parent,
    )
    contents: list[PairContent] = []

    with continue_feedback_scope(effective_continue_feedback):
        pending_en_md = {p.en_path for p in pairs}
        pending_en_tocs = {nav.en_path for nav in nav_pairs}

        def _read_en_toc_graph(path: str) -> str | None:
            # Prefer upstream main for EN toc/pages so strip_unreachable does not
            # use a stale source-PR checkout (#47108 bare ``{#T}`` after strip).
            if path.replace("\\", "/").startswith(f"{docs_root}/en/"):
                return read_text_at_commit(repo_path, merge_base_with, path)
            return read_ru(path)

        en_toc_reachable = build_en_toc_reachable_from_repo(
            repo_path,
            docs_root=docs_root,
            pending_en_md=pending_en_md,
            pending_en_tocs=pending_en_tocs,
            read_text=_read_en_toc_graph,
        )
        logger.info(
            "EN toc reachability: %s md paths (%s pending md, %s pending toc)",
            len(en_toc_reachable),
            len(pending_en_md),
            len(pending_en_tocs),
        )

        # Tombstone skip + orphan exemption must use tip redirects
        # (``merge_base_with``), not the merge-commit tree. Merged source PRs
        # checkout an old SHA where the page still lived; tip may already have
        # ``from`` → ``to`` while merge-era redirects.yaml does not (§6.242).
        redirects_yaml = (
            read_text_at_commit(repo_path, merge_base_with, f"{docs_root}/redirects.yaml")
            or ""
        )
        redirect_source_en = redirect_source_repo_md_paths(
            redirects_yaml,
            locale="en",
            docs_root=docs_root,
            candidate_repo_paths={pair.en_path for pair in pairs},
        )

        if pairs:
            contents = load_pair_contents(
                repo_path,
                pairs,
                merge_base_with=merge_base_with,
                ru_content_ref=ru_ref,
                ru_base_ref=ru_base_ref,
                authority=authority,
            )
            contents = _attach_source_coverage_plans(
                contents,
                scope_plan=scope_plan,
                authority=authority,
                checkpoint=active_checkpoint,
                resume_parent_run_id=resume_parent_run_id,
            )
            if any(
                content.coverage_plan is not None
                and content.coverage_plan.mode == "units"
                for content in contents
            ) and active_checkpoint is None:
                raise TranslationCheckpointError(
                    "units-mode translation requires a durable coverage evidence store"
                )
            # Always run real translation for doc_translate, including merged
            # source PRs. Routing merged PRs through critic-only verify planning
            # skipped any pair missing RU or EN text — so new RU pages never got
            # EN mirrors and deleted RU pages never removed EN (#45949 / #51696).
            # Historical EN preservation stays in differential translate +
            # localized mirror delta with merge_commit^ as RU base (§6.210).
            pr_result = run_pr_translation(
                contents,
                client,
                glossary,
                use_analyze_llm=False,
                config=cfg,
                en_toc_reachable=en_toc_reachable,
                redirect_source_en_paths=redirect_source_en,
                docs_text_reader=_docs_text_reader(
                    repo_path,
                    merge_base_with,
                    authority=authority,
                    docs_root=docs_root,
                ),
                docs_repo_path=repo_path,
                checkpoint=active_checkpoint,
                resume_parent_run_id=resume_parent_run_id,
            )
        else:
            pr_result = PRTranslationResult()

        _merge_yellow_warnings(pr_result, scope_plan.link_dep_warnings)

        if nav_pairs:
            pr_result.navigation_results = run_navigation_merges(
                nav_pairs,
                repo_path=repo_path,
                merge_base_with=merge_base_with,
                client=client,
                glossary=glossary,
                config=cfg,
                scope_plan=scope_plan,
                ru_content_ref=ru_ref,
                ru_base_ref=ru_base_ref,
                authority=authority,
                active_doc_ru_paths=frozenset(p.ru_path for p in pairs),
            )

    _materialize_soft_keep_blockers(
        pr_result,
        repo_path=repo_path,
        baseline_ref=merge_base_with,
    )

    # Orphan gate vs translation-branch tip (not stale merged-PR HEAD), §6.140.
    orphan_paths = apply_orphan_toc_page_checks(
        pr_result,
        repo_path=repo_path,
        docs_root=docs_root,
        baseline_ref=merge_base_with,
        exempt_en_paths=redirect_source_en,
    )
    pr_result.completeness_gaps = completeness_gaps(
        changes, pr_result, docs_root=cfg.paths.docs_root
    )
    if orphan_paths:
        logger.error(
            "Orphan EN pages after nav merge — treat as completeness gaps for PR #%s: %s",
            pr_number,
            orphan_paths,
        )
        pr_result.completeness_gaps = list(
            dict.fromkeys([*pr_result.completeness_gaps, *orphan_paths])
        )
    job.pr_result = pr_result

    deferred_outbound = _defer_proven_outbound_fragments(
        pr_result,
        repo_path=repo_path,
        baseline_ref=merge_base_with,
    )
    refresh_publication_impact(pr_result)
    if _publication_withheld(pr_result):
        _restore_deferred_outbound_fragments(deferred_outbound)
        refresh_publication_impact(pr_result)
        logger.error(
            "Publication withheld (%s) — skip commit/push for PR #%s: gaps=%s",
            pr_result.publication_impact.value,
            pr_number,
            pr_result.completeness_gaps,
        )
        touched = TouchedPaths([], [])
    else:
        touched = _apply_results_to_disk(
            repo_path,
            pr_result,
            dry_run=dry_run,
            docs_root=cfg.paths.docs_root,
        )
        redirects_path = f"{cfg.paths.docs_root}/redirects.yaml"
        if any(path == redirects_path for path, _kind in changes):
            redirects_current = (
                read_text_at_commit(repo_path, ru_ref, redirects_path)
                if ru_ref is not None
                else read_ru(redirects_path)
            ) or ""
            redirects_base = read_ru_base(redirects_path) or ""
            redirect_mappings = added_redirects(redirects_base, redirects_current)
            # Never retarget/write EN at redirects.yaml ``from`` paths — those are
            # tombstones. Source-branch leftovers + inbound retarget otherwise
            # recreate orphan EN pages on the translation branch (#45949 / #51703).
            impact_paths = retarget_redirect_inbound_links(
                repo_path,
                redirect_mappings,
                docs_root=cfg.paths.docs_root,
                dry_run=dry_run,
                allowed_paths=frozenset(redirect_impact_scope - redirect_source_en),
            )
            # Translation branches start from current upstream main. Never
            # write the historical source-merge copy of this global file:
            # doing so reverted unrelated redirects in #50901.
            redirects_worktree = (
                read_text_at_commit(repo_path, merge_base_with, redirects_path)
                or redirects_current
            )
            mirrored_redirects = mirror_redirects_to_en(redirects_worktree, redirect_mappings)
            if mirrored_redirects != redirects_worktree:
                impact_paths.append(redirects_path)
                if not dry_run:
                    write_text(repo_path, redirects_path, mirrored_redirects)
            touched = TouchedPaths(
                list(dict.fromkeys([*touched.written, *impact_paths])),
                touched.deleted,
            )

        late_budget = MarkdownDependencyBudget.from_state(
            scope_plan.dependency_budget.snapshot()
        )
        exact_declarations = _declare_exact_ascii_fragment_targets_after_apply(
            repo_path,
            touched.written,
            dry_run=dry_run,
            merge_base_with=merge_base_with,
            ru_content_ref=ru_ref,
            budget=late_budget,
            docs_root=docs_root,
        )
        _merge_yellow_warnings(pr_result, late_budget.warnings)
        if exact_declarations:
            touched = TouchedPaths(
                list(dict.fromkeys([*touched.written, *exact_declarations])),
                touched.deleted,
            )

        # After all EN targets and inbound retargets are on disk, remap any
        # leftover RU translit / Cyrillic fragments (§6.225 / #45949).
        late_repair = _repair_en_fragments_after_apply(
            repo_path,
            touched.written,
            dry_run=dry_run,
            merge_base_with=merge_base_with,
            ru_content_ref=ru_ref,
            docs_root=docs_root,
        )
        if late_repair:
            logger.info(
                "Late EN fragment repair on %d path(s): %s",
                len(late_repair),
                late_repair,
            )
            touched = TouchedPaths(
                list(dict.fromkeys([*touched.written, *late_repair])),
                touched.deleted,
            )

        reconciled_paths = _reconcile_final_en_same_fragment_paths_after_apply(
            repo_path,
            contents,
            pr_result,
            touched.written,
            dry_run=dry_run,
            merge_base_with=merge_base_with,
            ru_content_ref=ru_ref,
            deleted_paths=touched.deleted,
        )
        if reconciled_paths:
            logger.info(
                "Final EN same-fragment path reconciliation on %d path(s): %s",
                len(reconciled_paths),
                reconciled_paths,
            )

        _freeze_soft_keep_artifact_hashes(pr_result, repo_path=repo_path)

        # Final EN tree gate: href-only pairs skip per-file heuristics (§6.226).
        # Baseline = upstream tip EN so ambient tip link debt does not block
        # push when this PR did not introduce it (§6.228 / #40385).
        # Target resolution = tip + written overlays (§6.229): merge-commit
        # checkout must not make tip-only siblings look missing.
        en_written = {
            p
            for p in touched.written
            if p.endswith(".md") and "/docs/en/" in p.replace("\\", "/")
        }
        en_deleted = {
            p
            for p in touched.deleted
            if p.endswith(".md") and "/docs/en/" in p.replace("\\", "/")
        }
        final_tree_read = _final_tree_reader(
            repo_path,
            merge_base_with,
            en_written if not dry_run else set(),
            deleted_paths=en_deleted,
        )
        broken_links = apply_en_link_target_checks(
            pr_result,
            repo_path=repo_path,
            en_md_paths=en_written,
            baseline_read=lambda p: read_text_at_commit(repo_path, merge_base_with, p),
            docs_read=final_tree_read,
        )
        if broken_links:
            logger.error(
                "Broken EN link targets after apply — publish candidate as draft/RED "
                "for PR #%s: %s",
                pr_number,
                broken_links,
            )
            for path in broken_links:
                for run in pr_result.pair_results:
                    if run.plan.target_path.replace("\\", "/") != path:
                        continue
                    fr = run.file_result
                    if fr is None:
                        continue
                    for msg in fr.heuristic_blocking:
                        if msg.startswith("en_link_target:"):
                            logger.error("%s", msg)
        _recheck_deferred_outbound_fragments(
            pr_result,
            deferred_outbound,
            read_final_docs=final_tree_read,
            baseline_read_text=lambda path: read_text_at_commit(
                repo_path, merge_base_with, path
            ),
        )
        refresh_publication_impact(pr_result)
        if _publication_withheld(pr_result):
            logger.error(
                "Publication withheld after final-tree validation (%s) — "
                "skip commit/push for PR #%s",
                pr_result.publication_impact.value,
                pr_number,
            )
            touched = TouchedPaths([], [])

    if active_checkpoint is not None:
        _finish_translation_checkpoint(active_checkpoint, pr_result, scope_plan)

    preexisting_translation_pr: tuple[str, int] | None = None
    prepush_opened_pr: tuple[str, int, bool] | None = None
    pushed_candidate_sha: str | None = None
    coverage_evidence: CoverageEvidence | None = None
    push_receipt: RefMutationReceipt | None = None
    reused_existing_artifact_pr: _BoundArtifactPR | None = None
    committed = pushed = False
    if touched and not dry_run and not no_commit:
        prepare_translation_branch_on_base(
            repo_path,
            translation_branch=branch,
            base_remote_url=branch_remote_url,
            base_remote_name="ydbdoc-review-upstream",
            base_branch=branch_start_ref,
            paths=touched.written,
            base_commit_sha=translation_prepare_parent_sha,
            deleted_paths=touched.deleted,
        )
        msg = build_commit_message(pr_number, pr_result, config=cfg)
        committed = git_commit_paths(
            repo_path,
            touched.written,
            msg,
            _GITHUB_ACTOR_NAME,
            _GITHUB_ACTOR_EMAIL,
            deleted_paths=touched.deleted,
        )
        if committed:
            pushed_candidate_sha = _freeze_candidate_sha(repo_path)
            if active_checkpoint is not None:
                coverage_evidence = _persist_candidate_coverage_evidence(
                    repo_path=repo_path,
                    candidate_sha=pushed_candidate_sha,
                    authority=authority,
                    contents=contents,
                    store=active_checkpoint.store,
                    run_id=active_checkpoint.run_id,
                )
            # Every forced publication is an exact compare-and-swap. A normal
            # rerun must not overwrite a manual or concurrent branch update.
            if pr_result.publication_impact == PublicationImpact.PUBLISH_RED:
                # Keep discovery adjacent to the remote mutation. Any known ready
                # PR must become draft before its head is force-pushed.
                preexisting_translation_pr = gh.find_open_pull_by_head(
                    owner,
                    repo,
                    head_branch=branch,
                    base=translation_pr_base(ctx),
                )
                if preexisting_translation_pr is not None:
                    existing_pr_url, existing_pr_number = preexisting_translation_pr
                    gh.convert_pull_to_draft(owner, repo, existing_pr_number)
                    prepush_opened_pr = (
                        existing_pr_url,
                        existing_pr_number,
                        False,
                    )
                elif destination_lease.expected_sha is not None:
                    prepush_opened_pr = gh.create_pull(
                        owner,
                        repo,
                        title=f"Auto-translate docs from PR #{pr_number}",
                        head=branch,
                        base=translation_pr_base(ctx),
                        body=build_translation_pr_body(
                            pr_number,
                            github_repo,
                            publication_result=pr_result,
                        ),
                        draft=True,
                    )
                    if prepush_opened_pr is None:
                        logger.info(
                            "Existing translation branch %s has no publishable diff; "
                            "retry draft PR creation after pushing the candidate",
                            branch,
                        )
                    else:
                        _, existing_pr_number, created = prepush_opened_pr
                        if not created:
                            gh.convert_pull_to_draft(owner, repo, existing_pr_number)
            logger.info(
                "Pushing translation branch %s to %s/%s (from upstream %s, source PR head: %s)",
                branch,
                owner,
                repo,
                branch_start_ref,
                ctx.head_repo_full_name,
            )
            push_receipt = push_branch(
                repo_path,
                "ydbdoc-review-push",
                branch,
                push_token,
                upstream_url,
                force=True,
                guard_remote_ref=True,
                expected_remote_sha=destination_lease.expected_sha,
                source_sha=pushed_candidate_sha,
            )
            pushed = True
        elif pr_result.has_soft_keep:
            _require_remote_sha(
                gh,
                owner,
                repo,
                branch,
                destination_lease.expected_sha,
                context="before soft-keep artifact reuse",
            )
            reused_existing_artifact_pr = _matching_existing_soft_keep_artifact_pr(
                gh,
                owner,
                repo,
                branch=branch,
                base=translation_pr_base(ctx),
                result=pr_result,
                expected_remote_sha=destination_lease.expected_sha,
                repo_path=repo_path,
                source_repo=github_repo,
                source_pr=pr_number,
            )
            if reused_existing_artifact_pr is None:
                pr_result.publication_failure = "no_publishable_artifact"
                refresh_publication_impact(pr_result)
                job.blocked = True
    job.committed = committed
    job.pushed = pushed

    if dry_run:
        return job

    tr_pr_number: int | None = None
    tr_pr_url: str | None = None
    verify_result: PRTranslationResult | None = None
    artifact_provenance: TranslationArtifactProvenance | None = None
    if pushed or reused_existing_artifact_pr is not None:
        title = f"Auto-translate docs from PR #{pr_number}"
        publish_red = pr_result.publication_impact == PublicationImpact.PUBLISH_RED
        provisional_body = build_translation_pr_body(
            pr_number,
            github_repo,
            publication_result=pr_result,
        )
        expected_artifact_sha = (
            reused_existing_artifact_pr.bound_sha
            if reused_existing_artifact_pr is not None
            else pushed_candidate_sha
        )
        if expected_artifact_sha is None:
            raise RuntimeError("cannot publish PR metadata without an artifact SHA")
        _require_remote_sha(
            gh,
            owner,
            repo,
            branch,
            expected_artifact_sha,
            context="before translation PR metadata",
        )

        created = False
        if reused_existing_artifact_pr is not None:
            tr_pr_url = reused_existing_artifact_pr.url
            tr_pr_number = reused_existing_artifact_pr.number
        elif prepush_opened_pr is not None:
            tr_pr_url, tr_pr_number, created = prepush_opened_pr
        else:
            try:
                opened = gh.create_pull(
                    owner,
                    repo,
                    title=title,
                    head=branch,
                    base=translation_pr_base(ctx),
                    body=provisional_body,
                    draft=publish_red,
                )
                if opened is None:
                    raise RuntimeError(
                        f"pull request creation returned no publication for {branch}"
                    )
                tr_pr_url, tr_pr_number, created = opened
            except Exception as create_error:
                _raise_with_owned_rollback(
                    create_error,
                    push_receipt,
                    destination_lease.expected_sha,
                    repo_path=repo_path,
                    branch=branch,
                    push_token=push_token,
                    upstream_url=upstream_url,
                    message="translation PR creation and branch rollback both failed",
                )

        try:
            current_pull = gh.get_pull(owner, repo, tr_pr_number)
        except Exception as pull_error:
            _raise_with_owned_rollback(
                pull_error,
                push_receipt,
                destination_lease.expected_sha,
                repo_path=repo_path,
                branch=branch,
                push_token=push_token,
                upstream_url=upstream_url,
                message="translation PR lookup and branch rollback both failed",
            )
        head = current_pull.get("head") or {}
        current_head_sha = (
            str(head.get("sha") or "") if isinstance(head, dict) else ""
        )
        if current_head_sha != expected_artifact_sha:
            raise RuntimeError(
                f"PR #{tr_pr_number} head SHA changed: expected "
                f"{expected_artifact_sha}, found {current_head_sha or '<missing>'}"
            )

        artifact_provenance = (
            reused_existing_artifact_pr.provenance
            if reused_existing_artifact_pr is not None
            else bind_translation_artifact(
                repo_path,
                authority_selection,
                expected_artifact_sha,
            )
        )
        if coverage_evidence is not None:
            artifact_provenance = replace(
                artifact_provenance,
                coverage_version=coverage_evidence.version,
                coverage_run_id=active_checkpoint.run_id if active_checkpoint else None,
                coverage_digest=coverage_evidence.digest,
            )
        body = build_translation_pr_body(
            pr_number,
            github_repo,
            publication_result=pr_result,
            provenance=artifact_provenance,
        )

        job.translation_pr_url = tr_pr_url
        job.translation_pr_number = tr_pr_number
        try:
            if publish_red and current_pull.get("draft") is not True:
                gh.convert_pull_to_draft(owner, repo, tr_pr_number)
            gh.update_pull_body(owner, repo, tr_pr_number, body)
        except Exception as metadata_error:
            _raise_with_owned_rollback(
                metadata_error,
                push_receipt,
                destination_lease.expected_sha,
                repo_path=repo_path,
                branch=branch,
                push_token=push_token,
                upstream_url=upstream_url,
                message="translation PR metadata and branch rollback both failed",
            )
        if created:
            try:
                gh.add_issue_labels(owner, repo, tr_pr_number, ["documentation"])
            except GitHubAPIError as exc:
                logger.warning(
                    "Could not add documentation label to PR #%s: %s",
                    tr_pr_number,
                    exc,
                )

    if tr_pr_number is not None and pushed:
        _persist_continuability(
            repo_path,
            source_pr=pr_number,
            fixed_shas=fixed_shas,
            translation_pr=tr_pr_number,
            unfinished=True,
            unfinished_stage="verify",
            ops_ctx=ops_ctx,
        )
        verify_merge = f"origin/{translation_pr_base(ctx)}"
        logger.info(
            "Running inline doc_verify on translation PR #%s (merge_base=%s)",
            tr_pr_number,
            verify_merge,
        )
        verify_job = run_doc_verify(
            repo_path=repo_path,
            github_repo=github_repo,
            pr_number=tr_pr_number,
            merge_base_with=verify_merge,
            dry_run=False,
            no_commit=no_commit,
            config=cfg,
            inherited_completeness_gaps=pr_result.completeness_gaps,
            inherited_final_tree_blockers=pr_result.final_tree_blockers,
            continue_feedback=effective_continue_feedback,
            skip_ops_gates=True,
            _coverage_store=(
                active_checkpoint.store if active_checkpoint is not None else None
            ),
        )
        job.translation_comment_url = verify_job.translation_comment_url
        verify_result = verify_job.pr_result

    if not dry_run and not no_commit:
        unfinished_verify = (
            verify_result is not None
            and pr_result.publication_impact != PublicationImpact.PUBLISH_RED
            and _pr_result_has_blockers(verify_result)
        )
        _persist_continuability(
            repo_path,
            source_pr=pr_number,
            fixed_shas=fixed_shas,
            translation_pr=tr_pr_number,
            unfinished=unfinished_verify,
            unfinished_stage="verify",
            ops_ctx=ops_ctx,
        )

    elapsed = time.monotonic() - started
    meta = ReportMeta(mode="doc_translate", report_number=1, elapsed_s=elapsed)

    source_comment = build_source_pr_comment(
        pr_result,
        translation_pr_number=tr_pr_number,
        meta=meta,
        config=cfg,
        usage=client.usage_tracker,
        verify_result=verify_result,
        committed=committed,
    )
    later_ru_drift = build_later_ru_drift_report(
        repo_path,
        gh,
        owner=owner,
        repo=repo,
        source_head_sha=authority.source_head_sha,
        baseline_sha=authority.baseline_sha,
        source_paths=source_api_paths,
        docs_root=cfg.paths.docs_root,
    )
    if later_ru_drift:
        source_comment = f"{source_comment.rstrip()}\n\n{later_ru_drift}"

    job.source_comment_url = _safe_post_issue_comment(
        gh,
        owner,
        repo,
        pr_number,
        append_retention_footer(source_comment),
        label="source PR summary",
    )

    if ops_ctx is not None:
        usage = client.usage_tracker
        if job_requires_nonzero_exit(job, no_commit=no_commit):
            ops_status = "failed"
        elif pr_result.publication_impact == PublicationImpact.PUBLISH_RED:
            ops_status = "published_red"
        else:
            ops_status = "ok"
        finish_ops_job(
            ops_ctx,
            status=ops_status,
            cost_rub=usage.estimate_cost_rub(),
            input_tokens=sum((r.input_tokens or 0) for r in usage.records if r.success),
            output_tokens=sum((r.output_tokens or 0) for r in usage.records if r.success),
            translation_pr=tr_pr_number,
        )

    return job


def run_doc_verify(
    *,
    repo_path: str,
    github_repo: str,
    pr_number: int,
    merge_base_with: str = "origin/main",
    dry_run: bool = False,
    no_commit: bool = False,
    config: Config | None = None,
    inherited_completeness_gaps: list[str] | None = None,
    inherited_final_tree_blockers: list[FinalTreeBlocker] | None = None,
    continue_feedback: str | None = None,
    skip_ops_gates: bool = False,
    ops_mode: str = "verify",
    _fixup_rerun_depth: int = 0,
    _inline_fixup_context: PullRequestContext | None = None,
    _coverage_store: TranscriptStore | None = None,
    _ops_ctx: OpsContext | None = None,
    _verify_rerun_capability: object | None = None,
) -> DocJobResult:
    """``doc_verify`` on a translation PR, bilingual source PR, or verify fixup.

    Translation branch ``ydbdoc-review/pr-N``: EN from checkout, RU from source PR.
    Critic-fixup ``ydbdoc-review/verify-N``: re-verify original source scope; push
    inline onto the fixup head; full QA report stays on the fixup PR (§6.146).
    Other docs PRs (author/fork, RU+EN in one diff): both locales from checkout;
    completeness gaps flag RU changes without an EN mirror in the same PR (§6.135).
    When a new fixup PR is opened, the full report is posted there (not on source).
    """
    started = time.monotonic()
    cfg = config or load_config()
    api_token, push_token = _github_tokens(cfg)
    owner, repo = parse_repo(github_repo)
    gh = GitHubClient(api_token)
    active_rerun_capability = _ACTIVE_VERIFY_RERUN_CAPABILITY.get()
    internal_recursive_verify = (
        active_rerun_capability is not None
        and _verify_rerun_capability is active_rerun_capability
        and skip_ops_gates
        and _fixup_rerun_depth > 0
        and _inline_fixup_context is not None
    )
    if _inline_fixup_context is not None:
        if not internal_recursive_verify:
            if ops_mode == "continue" and skip_ops_gates:
                raise RuntimeError(
                    "continue ops admission cannot be skipped by an external verify"
                )
            raise RuntimeError(
                "inline fixup context requires an internal recursive verify"
            )
        ctx = _inline_fixup_context
    else:
        ctx = pull_request_context(gh, owner, repo, pr_number)
    translation_pr = is_translation_pr_branch(
        ctx.head_ref, translation_branch_prefix=cfg.paths.translation_branch_prefix
    )
    verify_fixup_pr = is_verify_fixup_branch(
        ctx.head_ref, verify_fixup_branch_prefix=cfg.paths.verify_fixup_branch_prefix
    )
    durable_final_tree_blockers = list(inherited_final_tree_blockers or ())
    if translation_pr:
        durable_final_tree_blockers = list(
            dict.fromkeys(
                [
                    *durable_final_tree_blockers,
                    *parse_final_tree_blocker_manifest(ctx.body),
                ]
            )
        )
    inherited_final_tree_blockers = durable_final_tree_blockers
    inherited_result = PRTranslationResult(
        completeness_gaps=list(inherited_completeness_gaps or ()),
        final_tree_blockers=list(durable_final_tree_blockers),
    )
    refresh_publication_impact(inherited_result)
    # Inline push (no separate fixup PR): translation heads and existing verify-* heads.
    inline_fixup_push = translation_pr or verify_fixup_pr
    source_pr = source_pr_number_from_branch(
        ctx.head_ref, prefix=cfg.paths.translation_branch_prefix
    )
    # Only parse "PR #N" from title/body on translation PRs. Bilingual author
    # PRs are self-contained; a title like "fix for PR #999" must not redirect RU.
    if source_pr is None and translation_pr:
        source_pr = parse_source_pr_from_text(f"{ctx.title}\n{ctx.body}")
    if source_pr is None and verify_fixup_pr:
        source_pr = source_pr_number_from_branch(
            ctx.head_ref, prefix=cfg.paths.verify_fixup_branch_prefix
        )
    source_pr_num = source_pr or pr_number
    requested_merge_base_sha = resolve_commit_ref(repo_path, merge_base_with)
    verify_content_sha = resolve_commit_ref(repo_path, "HEAD")
    artifact_provenance: TranslationArtifactProvenance | None = None
    if translation_pr:
        if source_pr is None:
            raise ValueError("translation PR source identity is missing")
        artifact_provenance = validate_authority_evidence(
            repo_path,
            parse_authority_evidence(ctx.body),
            expected_repo=github_repo,
            expected_source_pr=source_pr,
            current_candidate_sha=verify_content_sha,
        )
        merge_base_with = artifact_provenance.authority.baseline_sha
    else:
        merge_base_with = requested_merge_base_sha

    ops_ctx = _ops_ctx
    if ops_mode == "continue" and skip_ops_gates and not internal_recursive_verify:
        raise RuntimeError(
            "continue ops admission cannot be skipped by an external verify"
        )
    if ops_ctx is not None and (
        (skip_ops_gates and not internal_recursive_verify)
        or getattr(ops_ctx, "mode", None) != ops_mode
        or getattr(ops_ctx, "repo", None) != github_repo
        or getattr(ops_ctx, "source_pr", None) != source_pr_num
    ):
        raise RuntimeError("pre-authorized ops context does not match verify job")
    if not skip_ops_gates and ops_ctx is None:
        ops_ctx, gate, deny_body = begin_ops_job(
            mode=ops_mode,
            repo=github_repo,
            source_pr=source_pr_num,
            translation_pr=pr_number if inline_fixup_push else None,
            continue_feedback=continue_feedback,
        )
        if not gate.ok:
            if deny_body and not dry_run:
                _safe_post_issue_comment(gh, owner, repo, pr_number, deny_body, label="ops deny")
            return DocJobResult(
                mode=f"doc_{ops_mode}",
                pr_number=pr_number,
                source_pr_number=source_pr,
                dry_run=dry_run,
                pr_result=inherited_result,
                blocked=True,
            )
    if ops_mode == "continue":
        continuability = _load_continuability_for_continue(
            repo_path,
            source_pr_num,
            ops_store=getattr(ops_ctx, "store", None),
            parent_run_id=getattr(ops_ctx, "parent_run_id", None),
        )
        if (
            continuability is None
            or not continuability.allows_continue()
            or continuability.source_pr != source_pr_num
            or (
                continuability.translation_pr is not None
                and continuability.translation_pr != pr_number
            )
        ):
            if ops_ctx is not None and not dry_run:
                finish_ops_job(ops_ctx, status="failed", cost_rub=0.0)
            return DocJobResult(
                mode="doc_continue",
                pr_number=pr_number,
                source_pr_number=source_pr,
                translation_pr_number=pr_number if inline_fixup_push else None,
                dry_run=dry_run,
                pr_result=inherited_result,
                blocked=True,
            )

    coverage_evidence: CoverageEvidence | None = None
    attested_coverage_rebind = False
    trusted_coverage_store = _coverage_store or (
        getattr(ops_ctx, "store", None) if ops_ctx is not None else None
    )
    if artifact_provenance is not None and artifact_provenance.coverage_version is not None:
        if (
            trusted_coverage_store is None
            or artifact_provenance.coverage_run_id is None
            or artifact_provenance.coverage_digest is None
        ):
            raise ValueError("coverage evidence trusted store or binding is missing")
        try:
            coverage_evidence = load_coverage_evidence(
                trusted_coverage_store,
                artifact_provenance.coverage_run_id,
                candidate_sha=verify_content_sha,
                expected_digest=artifact_provenance.coverage_digest,
            )
        except CoverageEvidenceBindingMiss:
            if artifact_provenance.candidate_sha == verify_content_sha:
                raise
            coverage_evidence = load_attested_coverage_evidence(
                repo_path=repo_path,
                store=trusted_coverage_store,
                provenance=artifact_provenance,
                source_repo=github_repo,
                source_pr=source_pr_num,
                translation_pr=pr_number,
                new_candidate_sha=verify_content_sha,
            )
            attested_coverage_rebind = True
            logger.info(
                "Loaded exact coverage rebind attestation for translation PR #%s "
                "candidate %s",
                pr_number,
                verify_content_sha,
            )

    upstream_url = repo_https_clone_url(owner, repo)
    fixup_source_pr = source_pr or pr_number
    fixup_branch = verify_fixup_branch(cfg.paths.verify_fixup_branch_prefix, fixup_source_pr)
    fixup_base_ref, fixup_base_branch = translation_branch_base(ctx)
    fixup_pr_base = verify_fixup_pr_base(
        ctx, translation_branch_prefix=cfg.paths.translation_branch_prefix
    )
    destination_branch = ctx.head_ref if inline_fixup_push else fixup_branch
    destination_lease = _snapshot_destination_lease(
        gh,
        owner,
        repo,
        destination_branch,
    )
    if inline_fixup_push and (
        destination_lease.expected_sha is None
        or not ctx.head_sha
        or destination_lease.expected_sha != ctx.head_sha
        or destination_lease.expected_sha != verify_content_sha
    ):
        raise RuntimeError(
            "inline verify publication expectation changed: require nonempty "
            f"remote E == PR head == checkout C, got E="
            f"{destination_lease.expected_sha or '<absent>'}, "
            f"PR={ctx.head_sha or '<missing>'}, C={verify_content_sha}"
        )
    verify_prepare_parent_sha = (
        verify_content_sha
        if inline_fixup_push or (not is_fork_head(ctx) and not ctx.merged)
        else merge_base_with
    )

    changes = merge_pr_file_changes(
        list_pr_file_changes_git(repo_path, merge_base_with),
        list_pr_file_changes_api(gh, owner, repo, pr_number),
    )
    changes = filter_translate_changes(changes, cfg.paths.translate_skip_globs)
    if artifact_provenance is not None:
        source_changes = list(
            commit_changes_between(
                repo_path,
                artifact_provenance.authority.ru_base_sha,
                artifact_provenance.authority.ru_sha,
            )
        )
    else:
        source_changes = (
            list_pr_file_changes_api(gh, owner, repo, source_pr)
            if source_pr is not None
            else (None if translation_pr else changes)
        )
    if source_changes is not None:
        source_changes = filter_translate_changes(source_changes, cfg.paths.translate_skip_globs)
    # On verify-* continue/re-verify: re-check the original bilingual source scope
    # (not only the narrow fixup diff).
    pair_changes = source_changes if verify_fixup_pr and source_changes is not None else changes
    pairs = build_pairs_from_changes(pair_changes, docs_root=cfg.paths.docs_root)
    nav_pairs = build_verify_navigation_pairs(
        pair_changes,
        docs_root=cfg.paths.docs_root,
        source_changes=source_changes,
    )
    scope_plan = None
    expected_scope_pairs: list[DocPair] = []
    source_bilingual_skip: frozenset[str] = frozenset()
    redirect_tombstone_en: frozenset[str] = frozenset()
    recovered_late_en_paths: frozenset[str] = frozenset()
    if source_changes:
        read_ru, read_en_base, read_ru_base = make_repo_scope_readers(
            repo_path,
            merge_base_with,
            authority=(
                artifact_provenance.authority
                if artifact_provenance is not None
                else None
            ),
        )
        scope_plan = plan_translation_scope(
            source_changes,
            read_ru=read_ru,
            read_en_base=read_en_base,
            read_ru_base=read_ru_base,
            docs_root=cfg.paths.docs_root,
        )
        skip_globs = cfg.paths.translate_skip_globs
        if skip_globs:
            scope_plan = replace(
                scope_plan,
                doc_ru_paths=filter_path_set(scope_plan.doc_ru_paths, skip_globs),
                doc_from_diff=filter_path_set(scope_plan.doc_from_diff, skip_globs),
                doc_from_main=filter_path_set(scope_plan.doc_from_main, skip_globs),
                nav_ru_paths=filter_path_set(scope_plan.nav_ru_paths, skip_globs),
                nav_from_diff=filter_path_set(scope_plan.nav_from_diff, skip_globs),
                nav_from_main=filter_path_set(scope_plan.nav_from_main, skip_globs),
                doc_deleted=filter_path_set(scope_plan.doc_deleted, skip_globs),
            )
        if artifact_provenance is not None:
            recovered_late_en_paths = _reconstruct_late_dependency_budget_for_verify(
                repo_path,
                scope_plan,
                artifact_provenance,
                verified_commit_sha=verify_content_sha,
                docs_root=cfg.paths.docs_root,
            )
        nav_pairs = merge_navigation_pair_lists(
            navigation_pairs_from_plan(scope_plan, docs_root=cfg.paths.docs_root),
            nav_pairs,
        )
        source_bilingual_skip = frozenset(
            bilingual_en_mirrors(source_changes, docs_root=cfg.paths.docs_root)
        )
        verify_redirects_yaml = (
            read_text_at_commit(
                repo_path,
                merge_base_with,
                f"{cfg.paths.docs_root}/redirects.yaml",
            )
            or ""
        )
        redirect_tombstone_en = redirect_source_repo_md_paths(
            verify_redirects_yaml,
            locale="en",
            docs_root=cfg.paths.docs_root,
            candidate_repo_paths={
                counterpart(path, cfg.paths.docs_root)
                for path in scope_plan.doc_ru_paths
                if counterpart(path, cfg.paths.docs_root) is not None
            },
        )
        expected_scope_pairs = doc_pairs_from_plan(
            scope_plan,
            docs_root=cfg.paths.docs_root,
            skip_en_paths=source_bilingual_skip | redirect_tombstone_en,
        )
        _merge_yellow_warnings(inherited_result, scope_plan.link_dep_warnings)
    job = DocJobResult(
        mode="doc_verify",
        pr_number=pr_number,
        source_pr_number=source_pr,
        translation_branch=ctx.head_ref,
        translation_pr_number=pr_number,
        dry_run=dry_run,
        pr_result=inherited_result,
    )
    durable_impact_paths = frozenset(
        blocker.path.replace("\\", "/") for blocker in durable_final_tree_blockers
    )
    if not pairs and not nav_pairs:
        if translation_pr and not durable_impact_paths:
            logger.info("No doc or navigation pairs for verify on PR #%s", pr_number)
            return job
        logger.info(
            "No doc/nav pairs on bilingual/source PR #%s — completeness-only verify",
            pr_number,
        )

    translation_scope_missing: list[str] = []
    source_scope_en: frozenset[str] = frozenset()
    if translation_pr:
        noop_satisfied: set[str] = set()
        changed_en_paths = {path.replace("\\", "/") for path, _ in changes}
        if source_pr is not None:
            if artifact_provenance is not None:
                authority = artifact_provenance.authority
                for pair in expected_scope_pairs:
                    if pair.en_path in changed_en_paths:
                        continue
                    if href_only_source_noop_satisfied(
                        read_text_at_commit(
                            repo_path, authority.source_base_sha, pair.ru_path
                        ),
                        read_text_at_commit(
                            repo_path, authority.source_head_sha, pair.ru_path
                        ),
                        read_text_at_commit(repo_path, authority.ru_sha, pair.ru_path),
                        read_text_at_commit(repo_path, verify_content_sha, pair.en_path),
                    ):
                        noop_satisfied.add(pair.en_path)
            else:
                source_pull = gh.get_pull(owner, repo, source_pr)
                source_base_sha = str(source_pull.get("base", {}).get("sha") or "")
                source_head_sha = str(source_pull.get("head", {}).get("sha") or "")
                for pair in expected_scope_pairs:
                    if pair.en_path in changed_en_paths:
                        continue
                    if href_only_source_noop_satisfied(
                        gh.get_file_text(owner, repo, pair.ru_path, source_base_sha),
                        gh.get_file_text(owner, repo, pair.ru_path, source_head_sha),
                        read_text(repo_path, pair.ru_path),
                        read_text(repo_path, pair.en_path),
                    ):
                        noop_satisfied.add(pair.en_path)
        # Tip-inherited EN (same as upstream main, not rewritten this run) already
        # covers the source scope when RU/EN hrefs match (§6.231 / #51199
        # feature-not-supported identical noop).
        from ydbdoc_review.validation.href_parity import check_href_parity

        for pair in expected_scope_pairs:
            if pair.en_path in changed_en_paths or pair.en_path in noop_satisfied:
                continue
            en_tip = (
                read_text_at_commit(repo_path, verify_content_sha, pair.en_path)
                if artifact_provenance is not None
                else read_text(repo_path, pair.en_path)
            )
            ru_tip = (
                read_text_at_commit(
                    repo_path, artifact_provenance.authority.ru_sha, pair.ru_path
                )
                if artifact_provenance is not None
                else read_text(repo_path, pair.ru_path)
            )
            if en_tip is None or ru_tip is None:
                continue
            if not check_href_parity(ru_tip, en_tip):
                noop_satisfied.add(pair.en_path)
        # §6.243 / #52077: tip EN already covers inbound exact-ASCII fragments
        # from EN pages that *are* in this translation PR diff → no false gap
        # when translate was a tip noop (nothing to commit).
        from ydbdoc_review.pipeline.completeness import (
            tip_en_covers_inbound_fragments_from_changed,
        )

        changed_en_texts = {
            path: (
                read_text_at_commit(repo_path, verify_content_sha, path)
                if artifact_provenance is not None
                else read_text(repo_path, path)
            )
            or ""
            for path in changed_en_paths
            if path.endswith(".md")
        }
        for pair in expected_scope_pairs:
            if pair.en_path in changed_en_paths or pair.en_path in noop_satisfied:
                continue
            en_tip = (
                read_text_at_commit(repo_path, verify_content_sha, pair.en_path)
                if artifact_provenance is not None
                else read_text(repo_path, pair.en_path)
            )
            if not en_tip:
                continue
            if tip_en_covers_inbound_fragments_from_changed(
                pair.en_path,
                en_tip,
                changed_en_pages=changed_en_texts,
            ):
                noop_satisfied.add(pair.en_path)
        translation_scope_missing = translation_pr_scope_gaps(
            expected_scope_pairs,
            nav_pairs,
            changes,
            already_satisfied=(
                source_bilingual_skip | redirect_tombstone_en | frozenset(noop_satisfied)
            ),
        )
        # §6.240: source-PR scope wins over tip-ambient EN in the translation
        # branch diff (stale compare-configs / auth_config / tracing / …).
        source_scope_en = frozenset(p.en_path for p in expected_scope_pairs) | frozenset(
            recovered_late_en_paths
        )
        source_scope_nav_en: frozenset[str] | None = None
        if scope_plan is not None:
            source_scope_nav_en = frozenset(
                en
                for ru_nav in scope_plan.nav_ru_paths
                if (en := counterpart(ru_nav, cfg.paths.docs_root)) is not None
            )
        pairs, nav_pairs = filter_translation_pr_verify_scope(
            pairs,
            nav_pairs,
            changes,
            docs_root=cfg.paths.docs_root,
            allowed_en_paths=source_scope_en or None,
            allowed_nav_en_paths=source_scope_nav_en,
        )
        if source_scope_en:
            logger.info(
                "Translation PR #%s verify scoped to %s source EN md path(s) "
                "(dropped tip-ambient outside source PR)",
                pr_number,
                len(source_scope_en),
            )
        if (
            not pairs
            and not nav_pairs
            and not translation_scope_missing
            and not durable_impact_paths
            and coverage_evidence is None
        ):
            logger.info(
                "No scoped doc/navigation pairs for translation PR verify on #%s",
                pr_number,
            )
            return job

    client = create_llm_client(cfg)
    if ops_ctx is not None:
        client.transcript_recorder = ops_ctx.recorder
    glossary = load_glossary()

    if coverage_evidence is not None:
        if artifact_provenance is None:
            raise ValueError("coverage evidence authority provenance is missing")

        def _semantic_coverage_is_valid(
            target_path,
            unit,
            segments,
            translations,
        ) -> bool:
            del unit
            response = run_coverage_critic(
                client,
                segments=segments,
                translations=translations,
                glossary=glossary,
                file_path=target_path,
                source_lang="ru",
                target_lang="en",
                prompt_version=cfg.prompts.version,
                max_chars=cfg.translation.segments_per_batch_chars,
            )
            return response.verdict == "ok" and not response.issues

        validate_coverage_evidence(
            coverage_evidence,
            authority=artifact_provenance.authority,
            read_source=lambda path: read_text_at_commit(
                repo_path, artifact_provenance.authority.ru_sha, path
            ),
            read_baseline_en=lambda path: read_text_at_commit(
                repo_path, artifact_provenance.authority.baseline_sha, path
            ),
            read_candidate=lambda path: read_text_at_commit(
                repo_path, verify_content_sha, path
            ),
            semantic_validator=_semantic_coverage_is_valid,
        )

    pending_en_md = {p.en_path for p in pairs}
    pending_en_tocs = {nav.en_path for nav in nav_pairs}
    en_toc_reachable = build_en_toc_reachable_from_repo(
        repo_path,
        docs_root=cfg.paths.docs_root,
        pending_en_md=pending_en_md,
        pending_en_tocs=pending_en_tocs,
        read_text=_docs_text_reader(
            repo_path,
            verify_content_sha,
            authority=(
                artifact_provenance.authority
                if artifact_provenance is not None
                else None
            ),
            docs_root=cfg.paths.docs_root,
        ),
    )
    logger.info(
        "EN toc reachability (verify): %s md paths (%s pending md, %s pending toc)",
        len(en_toc_reachable),
        len(pending_en_md),
        len(pending_en_tocs),
    )

    with continue_feedback_scope(continue_feedback):
        if pairs:
            if source_pr is None:
                logger.info(
                    "doc_verify PR #%s: both locales from checkout (bilingual/source PR)",
                    pr_number,
                )
                contents = load_pair_contents(repo_path, pairs, merge_base_with=merge_base_with)
            else:
                contents = load_verify_pair_contents(
                    repo_path,
                    pairs,
                    merge_base_with=merge_base_with,
                    gh=gh,
                    owner=owner,
                    repo=repo,
                    source_pr=source_pr,
                    target_ref=verify_content_sha,
                    provenance=artifact_provenance,
                )
            if coverage_evidence is not None:
                plans_by_target = dict(coverage_evidence.plans)
                contents = [
                    replace(
                        content,
                        coverage_plan=plans_by_target.get(content.pair.en_path),
                    )
                    for content in contents
                ]
            pr_result = _run_verify_pairs(
                contents,
                client,
                glossary,
                cfg,
                en_toc_reachable=en_toc_reachable,
                docs_text_reader=_docs_text_reader(
                    repo_path,
                    verify_content_sha,
                    authority=(
                        artifact_provenance.authority
                        if artifact_provenance is not None
                        else None
                    ),
                    docs_root=cfg.paths.docs_root,
                ),
                docs_repo_path=repo_path,
            )
        else:
            pr_result = PRTranslationResult()

    if scope_plan is not None:
        _merge_yellow_warnings(pr_result, scope_plan.link_dep_warnings)

    md_en_paths = {p.en_path for p in pairs if not p.en_deleted}

    if nav_pairs:
        if source_pr is not None:
            ru_nav_texts = load_verify_navigation_ru_texts(
                nav_pairs,
                repo_path=repo_path,
                gh=gh,
                owner=owner,
                repo=repo,
                source_pr=source_pr,
                provenance=artifact_provenance,
            )
        else:
            ru_nav_texts = {}
            for nav in nav_pairs:
                if nav.ru_deleted:
                    continue
                text = read_text_at_commit(repo_path, verify_content_sha, nav.ru_path)
                if text is not None:
                    ru_nav_texts[nav.ru_path] = text

        pr_result.navigation_results = run_navigation_verifies(
            nav_pairs,
            repo_path=repo_path,
            merge_base_with=merge_base_with,
            ru_pr_by_path=ru_nav_texts,
            scope_plan=scope_plan,
            extra_toc_hrefs=(
                None if scope_plan is not None else extra_toc_hrefs_from_md_targets(md_en_paths)
            ),
            docs_root=cfg.paths.docs_root,
            active_doc_ru_paths=frozenset(p.ru_path for p in pairs),
            skip_globs=cfg.paths.translate_skip_globs,
            provenance=artifact_provenance,
            target_ref=verify_content_sha,
        )

    apply_include_parity_repair(
        pr_result,
        repo_path=repo_path,
        docs_root=cfg.paths.docs_root,
    )
    apply_include_target_checks(
        pr_result,
        repo_path=repo_path,
        docs_root=cfg.paths.docs_root,
    )
    apply_toc_target_checks(
        pr_result,
        repo_path=repo_path,
        pending_paths={
            r.plan.target_path
            for r in pr_result.pair_results
            if r.plan.target_lang == "en" and r.target_text is not None
        }
        | {n.en_path for n in pr_result.navigation_results if n.target_text is not None},
    )
    apply_orphan_toc_page_checks(
        pr_result,
        repo_path=repo_path,
        docs_root=cfg.paths.docs_root,
        baseline_ref=verify_content_sha,
        exempt_en_paths=redirect_tombstone_en
        or redirect_source_repo_md_paths(
            read_text_at_commit(
                repo_path,
                merge_base_with,
                f"{cfg.paths.docs_root}/redirects.yaml",
            )
            or "",
            locale="en",
            docs_root=cfg.paths.docs_root,
            candidate_repo_paths={pair.en_path for pair in pairs},
        ),
    )
    verify_en_paths = {
        r.plan.target_path.replace("\\", "/")
        for r in pr_result.pair_results
        if r.plan.target_lang == "en" and r.plan.target_path.endswith(".md")
    } | {
        path.replace("\\", "/")
        for path, _kind in changes
        if path.replace("\\", "/").startswith(f"{cfg.paths.docs_root}/en/")
        and path.endswith(".md")
        and (not source_scope_en or path.replace("\\", "/") in source_scope_en)
    } | set(durable_impact_paths)
    verify_deleted_en_paths = {
        path.replace("\\", "/")
        for path, kind in changes
        if kind == "deleted"
        and path.replace("\\", "/").startswith(f"{cfg.paths.docs_root}/en/")
        and path.endswith(".md")
    }
    rescannable_durable_paths = {
        path
        for path in durable_impact_paths
        if path not in verify_deleted_en_paths
        and (
            read_text_at_commit(repo_path, verify_content_sha, path) is not None
            or read_text_at_commit(repo_path, merge_base_with, path) is not None
        )
    }
    apply_en_link_target_checks(
        pr_result,
        repo_path=repo_path,
        en_md_paths=verify_en_paths,
        baseline_read=lambda p: read_text_at_commit(repo_path, merge_base_with, p),
        docs_read=_final_tree_reader(
            repo_path,
            verify_content_sha,
            set(),
            deleted_paths=verify_deleted_en_paths,
        ),
    )
    if not translation_pr:
        # Author/fork bilingual PR: flag RU docs/nav without EN mirror in the same diff.
        computed_gaps = completeness_gaps(changes, pr_result, docs_root=cfg.paths.docs_root)
        if computed_gaps:
            logger.info(
                "doc_verify bilingual completeness gaps on PR #%s: %s",
                pr_number,
                computed_gaps,
            )
        pr_result.completeness_gaps = list(
            dict.fromkeys([*pr_result.completeness_gaps, *computed_gaps])
        )
    if inherited_completeness_gaps:
        merged_gaps = list(
            dict.fromkeys([*inherited_completeness_gaps, *pr_result.completeness_gaps])
        )
        pr_result.completeness_gaps = merged_gaps
    if durable_final_tree_blockers:
        carried_link_blockers = [
            blocker
            for blocker in durable_final_tree_blockers
            if blocker.code == "en_link_target"
            and (
                blocker.path.replace("\\", "/") not in verify_en_paths
                or blocker.path.replace("\\", "/") not in rescannable_durable_paths
            )
        ]
        carried_soft_keep_blockers = [
            blocker
            for blocker in durable_final_tree_blockers
            if blocker.code == "translation_soft_keep"
            and not _soft_keep_is_manually_resolved(
                blocker,
                pr_result,
                repo_path=repo_path,
            )
        ]
        pr_result.final_tree_blockers = list(
            dict.fromkeys(
                [
                    *carried_link_blockers,
                    *carried_soft_keep_blockers,
                    *pr_result.final_tree_blockers,
                ]
            )
        )
    if translation_scope_missing:
        logger.error(
            "Translation PR #%s is missing source-scope EN paths: %s",
            pr_number,
            translation_scope_missing,
        )
        pr_result.completeness_gaps = list(
            dict.fromkeys([*pr_result.completeness_gaps, *translation_scope_missing])
        )

    refresh_publication_impact(pr_result)

    if attested_coverage_rebind:
        mismatches = _enforce_report_checkout_bytes(
            repo_path, verify_content_sha, pr_result
        )
        if mismatches:
            logger.error(
                "Attestation-backed doc_verify cannot publish critic changes; "
                "candidate %s remains unchanged and RED: %s",
                verify_content_sha,
                mismatches,
            )
            refresh_publication_impact(pr_result)

    verify_requires_red = result_has_blocking_findings(pr_result)
    if translation_pr and verify_requires_red:
        # Convert through the API method immediately before any local/remote
        # mutation. The client method re-fetches current state and confirms the
        # GraphQL draft transition instead of trusting the initial PR snapshot.
        gh.convert_pull_to_draft(owner, repo, pr_number)

    job.pr_result = pr_result

    final_read_only_verify = attested_coverage_rebind or (
        _fixup_rerun_depth >= 3 and inline_fixup_push
    )
    if final_read_only_verify:
        if attested_coverage_rebind:
            logger.info(
                "Attestation-backed doc_verify for PR #%s: preserving exact K and "
                "the complete PR body",
                pr_number,
            )
        else:
            logger.info(
                "Final read-only doc_verify for PR #%s: reporting the current head "
                "without applying further critic suggestions",
                pr_number,
            )
        touched = None
    else:
        wrapper_repairs = (
            _collect_frozen_baseline_wrapper_repairs(
                repo_path,
                pr_result,
                provenance=artifact_provenance,
                verified_commit_sha=verify_content_sha,
                source_scope_en=source_scope_en,
                docs_root=cfg.paths.docs_root,
            )
            if (
                translation_pr
                and artifact_provenance is not None
                and not dry_run
                and not no_commit
                and _fixup_rerun_depth < 3
            )
            else {}
        )
        if wrapper_repairs:
            for path in sorted(wrapper_repairs):
                write_text(repo_path, path, wrapper_repairs[path])
            touched = TouchedPaths(sorted(wrapper_repairs), [])
        else:
            touched = _apply_results_to_disk(
                repo_path,
                pr_result,
                dry_run=dry_run,
                docs_root=cfg.paths.docs_root,
            )
            if translation_pr and source_scope_en:
                protected_impact_paths = frozenset(
                    blocker.path.replace("\\", "/")
                    for blocker in inherited_final_tree_blockers or ()
                )
                ambient_restored = _restore_out_of_scope_en_from_base(
                    repo_path,
                    changes=changes,
                    allowed_en_paths=source_scope_en | protected_impact_paths,
                    merge_base_with=merge_base_with,
                    docs_root=cfg.paths.docs_root,
                    dry_run=dry_run,
                )
                if ambient_restored:
                    touched = TouchedPaths(
                        list(dict.fromkeys([*touched.written, *ambient_restored])),
                        touched.deleted,
                    )

    committed = pushed = False
    inline_head_changed = False
    verify_candidate_sha: str | None = None
    verify_push_receipt: RefMutationReceipt | None = None
    fixup_pr_number: int | None = None
    fixup_pr_url: str | None = None
    if touched and not dry_run and not no_commit:
        msg = build_commit_message(
            fixup_source_pr,
            pr_result,
            config=cfg,
            verify=True,
        )
        if inline_fixup_push:
            push_branch_name = ctx.head_ref
            prep_base_branch = ctx.head_ref
        else:
            push_branch_name = fixup_branch
            prep_base_branch = fixup_base_branch
        prepare_translation_branch_on_base(
            repo_path,
            translation_branch=push_branch_name,
            base_remote_url=fixup_base_ref,
            base_remote_name="ydbdoc-review-upstream",
            base_branch=prep_base_branch,
            paths=touched.written,
            base_commit_sha=verify_prepare_parent_sha,
            deleted_paths=touched.deleted,
        )
        head_before_fixup = git_head_sha(repo_path)
        committed = git_commit_paths(
            repo_path,
            touched.written,
            msg,
            _GITHUB_ACTOR_NAME,
            _GITHUB_ACTOR_EMAIL,
            deleted_paths=touched.deleted,
        )
        head_after_fixup = _freeze_candidate_sha(repo_path) if committed else None
        inline_head_changed = committed and head_after_fixup != head_before_fixup
        if committed:
            verify_candidate_sha = head_after_fixup
            if verify_candidate_sha is None:
                raise RuntimeError("cannot publish verify branch without candidate K")
            if translation_pr and coverage_evidence is not None:
                if (
                    trusted_coverage_store is None
                    or artifact_provenance is None
                    or artifact_provenance.coverage_run_id is None
                ):
                    raise TranslationCheckpointError(
                        "cannot bind repaired units candidate without trusted coverage store"
                    )
                coverage_evidence = _persist_repaired_coverage_evidence(
                    repo_path=repo_path,
                    candidate_sha=verify_candidate_sha,
                    prior=coverage_evidence,
                    store=trusted_coverage_store,
                    run_id=artifact_provenance.coverage_run_id,
                )
                artifact_provenance = replace(
                    artifact_provenance,
                    coverage_version=coverage_evidence.version,
                    coverage_digest=coverage_evidence.digest,
                )
            if translation_pr:
                logger.info(
                    "Pushing critic fixes onto translation branch %s (PR #%s)",
                    push_branch_name,
                    pr_number,
                )
            elif verify_fixup_pr:
                logger.info(
                    "Pushing critic fixes onto verify fixup branch %s (PR #%s)",
                    push_branch_name,
                    pr_number,
                )
            else:
                logger.info(
                    "Pushing doc_verify fixup branch %s to upstream (verified PR #%s head: %s)",
                    fixup_branch,
                    pr_number,
                    ctx.head_repo_full_name,
                )
            if inline_fixup_push or destination_lease.expected_sha == verify_candidate_sha:
                verify_push_receipt = push_branch(
                    repo_path,
                    "ydbdoc-review-push",
                    push_branch_name,
                    push_token,
                    upstream_url,
                    guard_remote_ref=True,
                    expected_remote_sha=destination_lease.expected_sha,
                    source_sha=verify_candidate_sha,
                )
            else:
                deleted_receipt: RefMutationReceipt | None = None
                if destination_lease.expected_sha is not None:
                    deleted_receipt = delete_remote_branch_with_lease(
                        repo_path,
                        "ydbdoc-review-push",
                        push_branch_name,
                        push_token,
                        upstream_url,
                        expected_remote_sha=destination_lease.expected_sha,
                    )
                try:
                    verify_push_receipt = push_branch(
                        repo_path,
                        "ydbdoc-review-push",
                        push_branch_name,
                        push_token,
                        upstream_url,
                        guard_remote_ref=True,
                        expected_remote_sha=None,
                        source_sha=verify_candidate_sha,
                    )
                except Exception as create_error:
                    if (
                        deleted_receipt is not None
                        and deleted_receipt.status is RefMutationStatus.CHANGED
                        and destination_lease.expected_sha is not None
                    ):
                        try:
                            remote_after_failure = gh.get_branch_sha(
                                owner,
                                repo,
                                push_branch_name,
                            )
                        except Exception as snapshot_error:
                            raise ExceptionGroup(
                                "verify candidate creation and remote inspection both failed",
                                [create_error, snapshot_error],
                            ) from None
                        if remote_after_failure is None:
                            try:
                                rollback_pushed_branch(
                                    repo_path,
                                    "ydbdoc-review-push",
                                    push_branch_name,
                                    push_token,
                                    upstream_url,
                                    expected_pushed_sha=None,
                                    previous_sha=destination_lease.expected_sha,
                                )
                            except Exception as restore_error:
                                raise ExceptionGroup(
                                    "verify candidate creation and branch restoration both failed",
                                    [create_error, restore_error],
                                ) from None
                    raise
            pushed = True
            _require_remote_sha(
                gh,
                owner,
                repo,
                push_branch_name,
                verify_candidate_sha,
                context="after verify branch publication",
            )
            if translation_pr and verify_requires_red:
                try:
                    gh.convert_pull_to_draft(owner, repo, pr_number)
                except Exception as confirmation_error:
                    _raise_with_owned_rollback(
                        confirmation_error,
                        verify_push_receipt,
                        destination_lease.expected_sha,
                        repo_path=repo_path,
                        branch=push_branch_name,
                        push_token=push_token,
                        upstream_url=upstream_url,
                        message=(
                            "RED verify draft confirmation and branch rollback "
                            "both failed"
                        ),
                    )
            if translation_pr and coverage_evidence is not None:
                if artifact_provenance is None:
                    raise RuntimeError("repaired coverage provenance is missing")
                rebound_body = build_translation_pr_body(
                    source_pr_num,
                    github_repo,
                    publication_result=pr_result,
                    provenance=artifact_provenance,
                )
                gh.update_pull_body(
                    owner,
                    repo,
                    pr_number,
                    rebound_body,
                )
                ctx = replace(ctx, body=rebound_body)
    job.committed = committed
    job.pushed = pushed

    # A report is evidence about one immutable checkout. Inline critic fixes
    # change the translation PR head, so the old result must never be posted as
    # current. Re-run verify on the new head and report that result (§6.219).
    if pushed and inline_fixup_push and inline_head_changed and not dry_run:
        if not verify_candidate_sha:
            raise RuntimeError("inline verify publication candidate SHA is missing")
        next_inline_context = _await_inline_fixup_pr_context(
            gh,
            owner,
            repo,
            pr_number,
            repo_path=repo_path,
            previous_context=ctx,
            expected_sha=verify_candidate_sha,
        )
        if _fixup_rerun_depth >= 2:
            logger.info(
                "Inline critic fix changed PR #%s after the automatic rerun limit; "
                "running one final read-only doc_verify on the new head",
                pr_number,
            )
        else:
            logger.info(
                "Inline critic fix changed PR #%s head; re-running doc_verify (%s/2)",
                pr_number,
                _fixup_rerun_depth + 1,
            )
        rerun_capability = object()
        capability_token = _ACTIVE_VERIFY_RERUN_CAPABILITY.set(rerun_capability)
        try:
            return run_doc_verify(
                repo_path=repo_path,
                github_repo=github_repo,
                pr_number=pr_number,
                merge_base_with=merge_base_with,
                dry_run=False,
                no_commit=no_commit,
                config=cfg,
                inherited_completeness_gaps=pr_result.completeness_gaps,
                inherited_final_tree_blockers=pr_result.final_tree_blockers,
                continue_feedback=continue_feedback,
                skip_ops_gates=True,
                ops_mode=ops_mode,
                _fixup_rerun_depth=_fixup_rerun_depth + 1,
                _inline_fixup_context=next_inline_context,
                _coverage_store=trusted_coverage_store,
                _ops_ctx=ops_ctx,
                _verify_rerun_capability=rerun_capability,
            )
        finally:
            _ACTIVE_VERIFY_RERUN_CAPABILITY.reset(capability_token)

    elapsed = time.monotonic() - started
    if dry_run:
        return job

    if pushed and not inline_fixup_push:
        title = f"Critic fixes for #{pr_number}"
        body = build_verify_fixup_pr_body(pr_number, github_repo, fixup_branch)
        try:
            opened = gh.create_pull(
                owner,
                repo,
                title=title,
                head=fixup_branch,
                base=fixup_pr_base,
                body=body,
            )
            if opened is None:
                raise RuntimeError(
                    f"pull request creation returned no publication for {fixup_branch}"
                )
            fixup_pr_url, fixup_pr_number, created = opened
        except Exception as create_error:
            _raise_with_owned_rollback(
                create_error,
                verify_push_receipt,
                destination_lease.expected_sha,
                repo_path=repo_path,
                branch=fixup_branch,
                push_token=push_token,
                upstream_url=upstream_url,
                message="verify PR creation and branch rollback both failed",
            )
        try:
            fixup_pull = gh.get_pull(owner, repo, fixup_pr_number)
        except Exception as pull_error:
            _raise_with_owned_rollback(
                pull_error,
                verify_push_receipt,
                destination_lease.expected_sha,
                repo_path=repo_path,
                branch=fixup_branch,
                push_token=push_token,
                upstream_url=upstream_url,
                message="verify PR lookup and branch rollback both failed",
            )
        fixup_head = fixup_pull.get("head") or {}
        fixup_head_sha = (
            str(fixup_head.get("sha") or "")
            if isinstance(fixup_head, dict)
            else ""
        )
        if fixup_head_sha != verify_candidate_sha:
            raise RuntimeError(
                f"PR #{fixup_pr_number} head SHA changed: expected "
                f"{verify_candidate_sha}, found {fixup_head_sha or '<missing>'}"
            )
        job.translation_pr_url = fixup_pr_url
        job.translation_pr_number = fixup_pr_number
        if created:
            try:
                gh.add_issue_labels(owner, repo, fixup_pr_number, ["documentation"])
            except GitHubAPIError as exc:
                logger.warning(
                    "Could not add documentation label to PR #%s: %s",
                    fixup_pr_number,
                    exc,
                )

    # Full QA report: on newly opened fixup PR when one exists; otherwise on
    # the verified PR (translation / verify-* / bilingual with no fixes).
    report_pr = fixup_pr_number if fixup_pr_number is not None else pr_number
    if attested_coverage_rebind:
        _require_remote_sha(
            gh,
            owner,
            repo,
            ctx.head_ref,
            verify_content_sha,
            context="before attestation-backed verify report",
        )
    if (
        translation_pr
        and source_pr is not None
        and not attested_coverage_rebind
    ):
        gh.update_pull_body(
            owner,
            repo,
            pr_number,
            build_translation_pr_body(
                source_pr,
                github_repo,
                publication_result=pr_result,
                provenance=artifact_provenance,
            ),
        )
    if final_read_only_verify:
        mismatches = _enforce_report_checkout_bytes(repo_path, verify_content_sha, pr_result)
        if mismatches:
            logger.error(
                "Refusing green evidence for checkout %s; in-memory QA differs: %s",
                verify_content_sha,
                mismatches,
            )
    report_num = _next_report_number(gh, owner, repo, report_pr)
    meta = ReportMeta(
        mode="doc_verify",
        report_number=report_num,
        elapsed_s=elapsed,
        checkout_ref=verify_content_sha,
    )
    job.translation_comment_url = _safe_post_issue_comment(
        gh,
        owner,
        repo,
        report_pr,
        append_retention_footer(
            build_full_report(
                pr_result,
                meta=meta,
                config=cfg,
                usage=client.usage_tracker,
                glossary=glossary,
                link=ReportLinkContext(github_repo=github_repo, ref=ctx.head_ref),
            )
        ),
        label="doc_verify QA report",
    )
    if fixup_pr_number is not None:
        job.source_comment_url = _safe_post_issue_comment(
            gh,
            owner,
            repo,
            pr_number,
            build_verify_fixup_source_comment(fixup_pr_number, translation_pr=translation_pr),
            label="doc_verify fixup link",
        )
    if ops_ctx is not None:
        usage = client.usage_tracker
        finish_ops_job(
            ops_ctx,
            status="ok",
            cost_rub=usage.estimate_cost_rub(),
            input_tokens=usage.total_input_tokens,
            output_tokens=usage.total_output_tokens,
            translation_pr=pr_number,
            report_text=None,
        )
    return job


def run_doc_continue(
    *,
    repo_path: str,
    github_repo: str,
    pr_number: int,
    merge_base_with: str = "origin/main",
    dry_run: bool = False,
    no_commit: bool = False,
    config: Config | None = None,
    instruction: str | None = None,
) -> DocJobResult:
    """Continue with operator feedback (label ``doc_continue``).

    ``pr_number`` is a **translation** PR (``ydbdoc-review/pr-N``) or a
    **verify fixup** PR (``ydbdoc-review/verify-N``, §6.146). Instruction comes
    from ``instruction`` or the latest ``/ydbdoc continue …`` comment on that PR.
    """
    cfg = config or load_config()
    api_token, _push = _github_tokens(cfg)
    owner, repo = parse_repo(github_repo)
    gh = GitHubClient(api_token)
    ctx = pull_request_context(gh, owner, repo, pr_number)

    feedback = (instruction or "").strip()
    if not feedback:
        comments = list(gh.iter_issue_comments(owner, repo, pr_number))
        found = find_latest_continue_instruction(comments)
        if not found:
            body = (
                "⛔ **ydbdoc-review:** не найдена инструкция "
                "`/ydbdoc continue …` в комментариях PR.\n\n"
                "Добавьте комментарий, например:\n"
                "```\n/ydbdoc continue use Wikipedia EN link for Sessions\n```\n"
                "и снова повесьте лейбл **`doc_continue``."
            )
            if not dry_run:
                _safe_post_issue_comment(gh, owner, repo, pr_number, body, label="continue missing")
            return DocJobResult(
                mode="doc_continue",
                pr_number=pr_number,
                dry_run=dry_run,
            )
        feedback = found

    source_pr = source_pr_number_from_branch(
        ctx.head_ref, prefix=cfg.paths.translation_branch_prefix
    )
    translation_source_pr = source_pr
    if source_pr is None and is_verify_fixup_branch(
        ctx.head_ref,
        verify_fixup_branch_prefix=cfg.paths.verify_fixup_branch_prefix,
    ):
        source_pr = source_pr_number_from_branch(
            ctx.head_ref,
            prefix=cfg.paths.verify_fixup_branch_prefix,
        )
    source_pr_num = source_pr or pr_number

    ops_ctx, gate, deny_body = begin_ops_job(
        mode="continue",
        repo=github_repo,
        source_pr=source_pr_num,
        translation_pr=pr_number,
        continue_feedback=feedback,
    )
    if not gate.ok:
        if deny_body and not dry_run:
            _safe_post_issue_comment(
                gh,
                owner,
                repo,
                pr_number,
                deny_body,
                label="ops deny",
            )
        return DocJobResult(
            mode="doc_continue",
            pr_number=pr_number,
            source_pr_number=source_pr,
            translation_pr_number=pr_number,
            dry_run=dry_run,
            blocked=True,
        )

    continuability = _load_continuability_for_continue(
        repo_path,
        source_pr_num,
        ops_store=getattr(ops_ctx, "store", None),
        parent_run_id=getattr(ops_ctx, "parent_run_id", None),
    )
    if (
        continuability is None
        or not continuability.allows_continue()
        or continuability.source_pr != source_pr_num
        or (
            continuability.translation_pr is not None
            and continuability.translation_pr != pr_number
        )
    ):
        body = (
            "⛔ **ydbdoc-review:** `doc_continue` отклонён: для исходного "
            f"PR #{source_pr_num} нет сохранённого незавершённого этапа после "
            "фиксации SHA. Запустите новый `doc_translate`."
        )
        if not dry_run:
            _safe_post_issue_comment(
                gh,
                owner,
                repo,
                pr_number,
                body,
                label="continue denied",
            )
            if ops_ctx is not None:
                finish_ops_job(ops_ctx, status="failed", cost_rub=0.0)
        return DocJobResult(
            mode="doc_continue",
            pr_number=pr_number,
            source_pr_number=source_pr,
            translation_pr_number=pr_number,
            dry_run=dry_run,
            blocked=True,
        )

    if translation_source_pr is not None:
        # A translation PR may be incomplete. Re-running verify can only edit
        # files already present in its diff, so it can never create an omitted
        # source-scope mirror (#50840). Continue must re-run translation from
        # the source PR, then perform its normal inline verify.
        job = run_doc_translate(
            repo_path=repo_path,
            github_repo=github_repo,
            pr_number=translation_source_pr,
            merge_base_with=merge_base_with,
            dry_run=dry_run,
            no_commit=no_commit,
            config=cfg,
            continue_feedback=feedback,
            ops_mode="continue",
            parent_run_id=getattr(ops_ctx, "parent_run_id", None),
            _ops_ctx=ops_ctx,
        )
    else:
        # Verify-fixup PRs have all source-scope files already; critic feedback
        # is applied inline without rebuilding a translation branch.
        job = run_doc_verify(
            repo_path=repo_path,
            github_repo=github_repo,
            pr_number=pr_number,
            merge_base_with=merge_base_with,
            dry_run=dry_run,
            no_commit=no_commit,
            config=cfg,
            continue_feedback=feedback,
            ops_mode="continue",
            _ops_ctx=ops_ctx,
        )
        if not dry_run and not no_commit:
            _persist_continuability(
                repo_path,
                source_pr=source_pr_num,
                fixed_shas=dict(continuability.fixed_shas),
                translation_pr=pr_number,
                unfinished=(
                    not job.blocked and _pr_result_has_blockers(job.pr_result)
                ),
                unfinished_stage="verify",
                ops_ctx=ops_ctx,
            )
    job.mode = "doc_continue"
    return job
