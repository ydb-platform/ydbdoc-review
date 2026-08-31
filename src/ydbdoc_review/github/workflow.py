"""GitHub Actions workflow: doc_translate and doc_verify."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from ydbdoc_review.config.loader import Config, load_config
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.github.errors import GitHubAPIError, GitHubConfigError
from ydbdoc_review.github.git_ops import (
    ensure_commit,
    git_commit_paths,
    git_head_sha,
    prepare_translation_branch_on_base,
    push_branch,
    read_text,
    read_text_at_ref,
    read_text_at_upstream_tip,
    write_text,
)
from ydbdoc_review.github.pr import (
    build_pairs_from_changes,
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
    source_pr_scope_changes,
    translate_ru_content_ref,
    translation_branch_base,
    translation_pr_base,
    verify_fixup_branch,
    verify_fixup_pr_base,
)
from ydbdoc_review.harness.pr_context import PRHarnessContext
from ydbdoc_review.harness.pr_profiles import VERIFY_PR_PROFILE
from ydbdoc_review.harness.pr_runner import PRHarness
from ydbdoc_review.harness.pr_state import PRRunState
from ydbdoc_review.llm.client import create_llm_client
from ydbdoc_review.navigation.redirects import redirect_source_repo_md_paths
from ydbdoc_review.navigation.scope_planner import (
    doc_pairs_from_plan,
    make_repo_scope_readers,
    merge_navigation_pair_lists,
    navigation_pairs_from_plan,
    plan_translation_scope,
    synthetic_changes_from_plan,
)
from ydbdoc_review.ops.continue_cmd import find_latest_continue_instruction
from ydbdoc_review.ops.feedback_ctx import continue_feedback_scope
from ydbdoc_review.ops.lifecycle import (
    append_retention_footer,
    begin_ops_job,
    compose_continue_feedback,
    finish_ops_job,
    load_parent_run_context,
)
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
from ydbdoc_review.pipeline.skip_paths import filter_path_set, filter_translate_changes
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    build_commit_message,
    build_full_report,
    build_source_pr_comment,
    build_translation_pr_body,
    build_verify_fixup_pr_body,
    build_verify_fixup_source_comment,
)
from ydbdoc_review.reporting.locations import ReportLinkContext
from ydbdoc_review.translation.glossary import Glossary, load_glossary
from ydbdoc_review.validation.glossary_toc_links import build_en_toc_reachable_from_repo
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
        committed = read_text_at_ref(repo_path, checkout_ref, pair.plan.target_path)
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


def _docs_text_reader(repo_path: str, merge_base_with: str):
    """Read docs paths from worktree, else upstream tip (§6.142 fragment repair)."""

    def _read(path: str) -> str | None:
        text = read_text(repo_path, path)
        if text is not None:
            return text
        return read_text_at_upstream_tip(repo_path, merge_base_with, path)

    return _read


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
) -> DocJobResult:
    """Full ``doc_translate`` workflow for a source PR."""
    started = time.monotonic()
    cfg = config or load_config()
    api_token, push_token = _github_tokens(cfg)
    owner, repo = parse_repo(github_repo)
    gh = GitHubClient(api_token)

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
        )

    effective_continue_feedback = continue_feedback or (
        ops_ctx.continue_feedback if ops_ctx else None
    )
    if ops_mode == "continue" and ops_ctx is not None:
        effective_continue_feedback = compose_continue_feedback(
            effective_continue_feedback,
            load_parent_run_context(ops_ctx),
        )

    ctx = pull_request_context(gh, owner, repo, pr_number)
    branch = f"{cfg.paths.translation_branch_prefix}{pr_number}"
    upstream_url = repo_https_clone_url(owner, repo)
    branch_remote_url, branch_start_ref = translation_branch_base(ctx)

    ru_ref = translate_ru_content_ref(ctx)
    ru_base_ref: str | None = None
    if ru_ref is not None:
        if ensure_commit(repo_path, ru_ref):
            logger.info(
                "Merged source PR #%s: reading RU from merge commit %s",
                pr_number,
                ru_ref[:12],
            )
            # A merged PR's original RU delta is merge_commit^..merge_commit.
            # Comparing it with current main makes old source changes look like
            # no-ops and silently preserves stale EN (§6.210 / #40385).
            ru_base_ref = f"{ru_ref}^"
        else:
            logger.warning(
                "Merged source PR #%s: merge commit %s not fetchable; "
                "falling back to checkout HEAD for RU",
                pr_number,
                ru_ref[:12],
            )
            ru_ref = None

    changes = source_pr_scope_changes(
        ctx,
        list_pr_file_changes_git(repo_path, merge_base_with),
        list_pr_file_changes_api(gh, owner, repo, pr_number),
    )
    changes = filter_translate_changes(changes, cfg.paths.translate_skip_globs)
    docs_root = cfg.paths.docs_root
    read_ru, read_en_base, read_ru_base = make_repo_scope_readers(
        repo_path,
        merge_base_with,
        ru_content_ref=ru_ref,
        ru_base_ref=ru_base_ref,
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
        from ydbdoc_review.navigation.scope_planner import TranslationScopePlan

        scope_plan = TranslationScopePlan(
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
    if not pairs and not nav_pairs:
        logger.info("No doc or navigation pairs in PR #%s", pr_number)
        # Bilingual RU+EN in the same source PR are dropped from ``pairs`` via
        # ``skip_en_paths`` before analyze — still post «перевод не требуется»
        # (§6.76 / #48751). Without this early path the comment never appeared.
        pr_result = _pr_result_for_bilingual_skips(bilingual_skip, docs_root=docs_root)
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

    with continue_feedback_scope(effective_continue_feedback):
        pending_en_md = {p.en_path for p in pairs}
        pending_en_tocs = {nav.en_path for nav in nav_pairs}

        def _read_en_toc_graph(path: str) -> str | None:
            # Prefer upstream main for EN toc/pages so strip_unreachable does not
            # use a stale source-PR checkout (#47108 bare ``{#T}`` after strip).
            if path.replace("\\", "/").startswith(f"{docs_root}/en/"):
                text = read_text_at_ref(repo_path, merge_base_with, path)
                if text is not None:
                    return text
            return read_text(repo_path, path)

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

        redirects_yaml = (
            read_text_at_ref(repo_path, ru_ref, f"{docs_root}/redirects.yaml")
            if ru_ref
            else None
        ) or (
            read_text_at_ref(repo_path, merge_base_with, f"{docs_root}/redirects.yaml")
            or read_text(repo_path, f"{docs_root}/redirects.yaml")
            or ""
        )
        redirect_source_en = redirect_source_repo_md_paths(
            redirects_yaml, locale="en", docs_root=docs_root
        )

        if pairs:
            contents = load_pair_contents(
                repo_path,
                pairs,
                merge_base_with=merge_base_with,
                ru_content_ref=ru_ref,
                ru_base_ref=ru_base_ref,
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
                docs_text_reader=_docs_text_reader(repo_path, merge_base_with),
                docs_repo_path=repo_path,
            )
        else:
            pr_result = PRTranslationResult()

        md_en_paths = {
            r.plan.target_path
            for r in pr_result.pair_results
            if r.target_text is not None and not r.error
        }

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
                active_doc_ru_paths=frozenset(p.ru_path for p in pairs),
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

    if pr_result.completeness_gaps:
        logger.error(
            "Completeness gaps — skip commit/push for PR #%s: %s",
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
                read_text_at_ref(repo_path, ru_ref, redirects_path)
                if ru_ref
                else read_text(repo_path, redirects_path)
            ) or ""
            redirects_base = (
                read_text_at_ref(repo_path, ru_base_ref or merge_base_with, redirects_path) or ""
            )
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
                read_text_at_ref(repo_path, merge_base_with, redirects_path)
                or read_text(repo_path, redirects_path)
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

    committed = pushed = False
    if touched and not dry_run and not no_commit:
        prepare_translation_branch_on_base(
            repo_path,
            translation_branch=branch,
            base_remote_url=branch_remote_url,
            base_remote_name="ydbdoc-review-upstream",
            base_branch=branch_start_ref,
            paths=touched.written,
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
            logger.info(
                "Pushing translation branch %s to %s/%s (from upstream %s, source PR head: %s)",
                branch,
                owner,
                repo,
                branch_start_ref,
                ctx.head_repo_full_name,
            )
            push_branch(
                repo_path,
                "ydbdoc-review-push",
                branch,
                push_token,
                upstream_url,
                force=True,
            )
            pushed = True
    job.committed = committed
    job.pushed = pushed

    if dry_run:
        return job

    tr_pr_number: int | None = None
    tr_pr_url: str | None = None
    verify_result: PRTranslationResult | None = None
    if pushed:
        title = f"Auto-translate docs from PR #{pr_number}"
        body = build_translation_pr_body(pr_number, github_repo)
        opened = gh.create_pull(
            owner,
            repo,
            title=title,
            head=branch,
            base=translation_pr_base(ctx),
            body=body,
        )
        if opened:
            tr_pr_url, tr_pr_number, created = opened
            job.translation_pr_url = tr_pr_url
            job.translation_pr_number = tr_pr_number
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
            continue_feedback=effective_continue_feedback,
            skip_ops_gates=True,
        )
        job.translation_comment_url = verify_job.translation_comment_url
        verify_result = verify_job.pr_result

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
                translation_pr_number=tr_pr_number,
                meta=meta,
                config=cfg,
                usage=client.usage_tracker,
                verify_result=verify_result,
                committed=committed,
            )
        ),
        label="source PR summary",
    )

    if ops_ctx is not None:
        usage = client.usage_tracker
        finish_ops_job(
            ops_ctx,
            status="ok" if not pr_result.failed_count else "failed",
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
    continue_feedback: str | None = None,
    skip_ops_gates: bool = False,
    ops_mode: str = "verify",
    _fixup_rerun_depth: int = 0,
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

    ctx = pull_request_context(gh, owner, repo, pr_number)
    translation_pr = is_translation_pr_branch(
        ctx.head_ref, translation_branch_prefix=cfg.paths.translation_branch_prefix
    )
    verify_fixup_pr = is_verify_fixup_branch(
        ctx.head_ref, verify_fixup_branch_prefix=cfg.paths.verify_fixup_branch_prefix
    )
    # Inline push (no separate fixup PR): translation heads and existing verify-* heads.
    inline_fixup_push = translation_pr or verify_fixup_pr
    source_pr = source_pr_number_from_branch(
        ctx.head_ref, prefix=cfg.paths.translation_branch_prefix
    )
    # Only parse "PR #N" from title/body on translation PRs. Bilingual author
    # PRs are self-contained; a title like "fix for PR #999" must not redirect RU.
    if source_pr is None and translation_pr:
        pull_body = str(gh.get_pull(owner, repo, pr_number).get("body") or "")
        source_pr = parse_source_pr_from_text(f"{ctx.title}\n{pull_body}")
    if source_pr is None and verify_fixup_pr:
        source_pr = source_pr_number_from_branch(
            ctx.head_ref, prefix=cfg.paths.verify_fixup_branch_prefix
        )
    source_pr_num = source_pr or pr_number

    ops_ctx = None
    if not skip_ops_gates:
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
            )

    upstream_url = repo_https_clone_url(owner, repo)
    fixup_source_pr = source_pr or pr_number
    fixup_branch = verify_fixup_branch(cfg.paths.verify_fixup_branch_prefix, fixup_source_pr)
    fixup_base_ref, fixup_base_branch = translation_branch_base(ctx)
    fixup_pr_base = verify_fixup_pr_base(
        ctx, translation_branch_prefix=cfg.paths.translation_branch_prefix
    )

    # Re-run must not reuse a stale fixup branch/PR (closes open fixup PRs).
    # Never delete the branch we are currently verifying (verify-* head).
    if not inline_fixup_push and not dry_run:
        _delete_stale_verify_fixup(gh, owner, repo, fixup_branch)

    # Content under review (PR tip / merge commit). Capture before prepare_*
    # moves HEAD onto main for the fixup branch (§6.137).
    verify_content_sha = git_head_sha(repo_path)

    changes = merge_pr_file_changes(
        list_pr_file_changes_git(repo_path, merge_base_with),
        list_pr_file_changes_api(gh, owner, repo, pr_number),
    )
    changes = filter_translate_changes(changes, cfg.paths.translate_skip_globs)
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
    if source_changes:
        read_ru, read_en_base, read_ru_base = make_repo_scope_readers(repo_path, merge_base_with)
        scope_plan = plan_translation_scope(
            source_changes,
            read_ru=read_ru,
            read_en_base=read_en_base,
            read_ru_base=read_ru_base,
            docs_root=cfg.paths.docs_root,
        )
        skip_globs = cfg.paths.translate_skip_globs
        if skip_globs:
            from ydbdoc_review.navigation.scope_planner import TranslationScopePlan

            scope_plan = TranslationScopePlan(
                doc_ru_paths=filter_path_set(scope_plan.doc_ru_paths, skip_globs),
                doc_from_diff=filter_path_set(scope_plan.doc_from_diff, skip_globs),
                doc_from_main=filter_path_set(scope_plan.doc_from_main, skip_globs),
                nav_ru_paths=filter_path_set(scope_plan.nav_ru_paths, skip_globs),
                nav_from_diff=filter_path_set(scope_plan.nav_from_diff, skip_globs),
                nav_from_main=filter_path_set(scope_plan.nav_from_main, skip_globs),
            )
        nav_pairs = merge_navigation_pair_lists(
            navigation_pairs_from_plan(scope_plan, docs_root=cfg.paths.docs_root),
            nav_pairs,
        )
        source_bilingual_skip = frozenset(
            bilingual_en_mirrors(source_changes, docs_root=cfg.paths.docs_root)
        )
        expected_scope_pairs = doc_pairs_from_plan(
            scope_plan,
            docs_root=cfg.paths.docs_root,
            skip_en_paths=source_bilingual_skip,
        )
    job = DocJobResult(
        mode="doc_verify",
        pr_number=pr_number,
        source_pr_number=source_pr,
        translation_branch=ctx.head_ref,
        translation_pr_number=pr_number,
        dry_run=dry_run,
    )
    if not pairs and not nav_pairs:
        if translation_pr:
            logger.info("No doc or navigation pairs for verify on PR #%s", pr_number)
            return job
        logger.info(
            "No doc/nav pairs on bilingual/source PR #%s — completeness-only verify",
            pr_number,
        )

    translation_scope_missing: list[str] = []
    if translation_pr:
        noop_satisfied: set[str] = set()
        if source_pr is not None:
            source_pull = gh.get_pull(owner, repo, source_pr)
            source_base_sha = str(source_pull.get("base", {}).get("sha") or "")
            source_head_sha = str(source_pull.get("head", {}).get("sha") or "")
            changed_en_paths = {path.replace("\\", "/") for path, _ in changes}
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
        translation_scope_missing = translation_pr_scope_gaps(
            expected_scope_pairs,
            nav_pairs,
            changes,
            already_satisfied=source_bilingual_skip | frozenset(noop_satisfied),
        )
        pairs, nav_pairs = filter_translation_pr_verify_scope(
            pairs,
            nav_pairs,
            changes,
            docs_root=cfg.paths.docs_root,
        )
        if not pairs and not nav_pairs and not translation_scope_missing:
            logger.info(
                "No scoped doc/navigation pairs for translation PR verify on #%s",
                pr_number,
            )
            return job

    client = create_llm_client(cfg)
    if ops_ctx is not None:
        client.transcript_recorder = ops_ctx.recorder
    glossary = load_glossary()

    pending_en_md = {p.en_path for p in pairs}
    pending_en_tocs = {nav.en_path for nav in nav_pairs}
    en_toc_reachable = build_en_toc_reachable_from_repo(
        repo_path,
        docs_root=cfg.paths.docs_root,
        pending_en_md=pending_en_md,
        pending_en_tocs=pending_en_tocs,
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
                )
            pr_result = _run_verify_pairs(
                contents,
                client,
                glossary,
                cfg,
                en_toc_reachable=en_toc_reachable,
                docs_text_reader=_docs_text_reader(repo_path, merge_base_with),
                docs_repo_path=repo_path,
            )
        else:
            pr_result = PRTranslationResult()

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
            )
        else:
            ru_nav_texts = {}
            for nav in nav_pairs:
                if nav.ru_deleted:
                    continue
                text = read_text(repo_path, nav.ru_path)
                if text is None:
                    text = read_text_at_ref(repo_path, "HEAD", nav.ru_path)
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
    verify_redirects_yaml = (
        read_text_at_ref(repo_path, merge_base_with, f"{cfg.paths.docs_root}/redirects.yaml")
        or read_text(repo_path, f"{cfg.paths.docs_root}/redirects.yaml")
        or ""
    )
    apply_orphan_toc_page_checks(
        pr_result,
        repo_path=repo_path,
        docs_root=cfg.paths.docs_root,
        exempt_en_paths=redirect_source_repo_md_paths(
            verify_redirects_yaml, locale="en", docs_root=cfg.paths.docs_root
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
    if translation_scope_missing:
        logger.error(
            "Translation PR #%s is missing source-scope EN paths: %s",
            pr_number,
            translation_scope_missing,
        )
        pr_result.completeness_gaps = list(
            dict.fromkeys([*pr_result.completeness_gaps, *translation_scope_missing])
        )

    job.pr_result = pr_result

    final_read_only_verify = _fixup_rerun_depth >= 3 and inline_fixup_push
    if final_read_only_verify:
        logger.info(
            "Final read-only doc_verify for PR #%s: reporting the current head "
            "without applying further critic suggestions",
            pr_number,
        )
        touched = None
    else:
        touched = _apply_results_to_disk(
            repo_path,
            pr_result,
            dry_run=dry_run,
            docs_root=cfg.paths.docs_root,
        )

    committed = pushed = False
    inline_head_changed = False
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
        inline_head_changed = committed and git_head_sha(repo_path) != head_before_fixup
        if committed:
            if not inline_fixup_push:
                _delete_stale_verify_fixup(gh, owner, repo, fixup_branch)
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
            push_branch(
                repo_path,
                "ydbdoc-review-push",
                push_branch_name,
                push_token,
                upstream_url,
            )
            pushed = True
    job.committed = committed
    job.pushed = pushed

    # A report is evidence about one immutable checkout. Inline critic fixes
    # change the translation PR head, so the old result must never be posted as
    # current. Re-run verify on the new head and report that result (§6.219).
    if pushed and inline_fixup_push and inline_head_changed and not dry_run:
        if _fixup_rerun_depth >= 2:
            logger.info(
                "Inline critic fix changed PR #%s after the automatic rerun limit; "
                "running one final read-only doc_verify on the new head",
                pr_number,
            )
            return run_doc_verify(
                repo_path=repo_path,
                github_repo=github_repo,
                pr_number=pr_number,
                merge_base_with=merge_base_with,
                dry_run=False,
                no_commit=no_commit,
                config=cfg,
                inherited_completeness_gaps=inherited_completeness_gaps,
                continue_feedback=continue_feedback,
                skip_ops_gates=True,
                ops_mode=ops_mode,
                _fixup_rerun_depth=_fixup_rerun_depth + 1,
            )
        else:
            logger.info(
                "Inline critic fix changed PR #%s head; re-running doc_verify (%s/2)",
                pr_number,
                _fixup_rerun_depth + 1,
            )
            return run_doc_verify(
                repo_path=repo_path,
                github_repo=github_repo,
                pr_number=pr_number,
                merge_base_with=merge_base_with,
                dry_run=False,
                no_commit=no_commit,
                config=cfg,
                inherited_completeness_gaps=inherited_completeness_gaps,
                continue_feedback=continue_feedback,
                skip_ops_gates=True,
                ops_mode=ops_mode,
                _fixup_rerun_depth=_fixup_rerun_depth + 1,
            )

    elapsed = time.monotonic() - started
    if dry_run:
        return job

    if pushed and not inline_fixup_push:
        title = f"Critic fixes for #{pr_number}"
        body = build_verify_fixup_pr_body(pr_number, github_repo, fixup_branch)
        opened = gh.create_pull(
            owner,
            repo,
            title=title,
            head=fixup_branch,
            base=fixup_pr_base,
            body=body,
        )
        if opened:
            fixup_pr_url, fixup_pr_number, created = opened
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
    if source_pr is not None:
        # A translation PR may be incomplete. Re-running verify can only edit
        # files already present in its diff, so it can never create an omitted
        # source-scope mirror (#50840). Continue must re-run translation from
        # the source PR, then perform its normal inline verify.
        job = run_doc_translate(
            repo_path=repo_path,
            github_repo=github_repo,
            pr_number=source_pr,
            merge_base_with=merge_base_with,
            dry_run=dry_run,
            no_commit=no_commit,
            config=cfg,
            continue_feedback=feedback,
            ops_mode="continue",
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
        )
    job.mode = "doc_continue"
    return job
