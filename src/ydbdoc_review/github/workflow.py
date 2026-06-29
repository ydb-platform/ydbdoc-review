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
    git_commit_paths,
    git_head_sha,
    prepare_translation_branch_on_base,
    push_branch,
    read_text,
    read_text_at_ref,
    write_text,
)
from ydbdoc_review.github.pr import (
    build_pairs_from_changes,
    list_pr_file_changes_api,
    list_pr_file_changes_git,
    load_pair_contents,
    load_verify_navigation_ru_texts,
    load_verify_pair_contents,
    parse_repo,
    parse_source_pr_from_text,
    pull_request_context,
    repo_https_clone_url,
    source_pr_number_from_branch,
    translation_branch_base,
    translation_pr_base,
    verify_fixup_branch,
    verify_fixup_pr_base,
)
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.harness.pr_context import PRHarnessContext
from ydbdoc_review.harness.pr_profiles import VERIFY_PR_PROFILE
from ydbdoc_review.harness.pr_runner import PRHarness
from ydbdoc_review.harness.pr_state import PRRunState
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.completeness import completeness_gaps
from ydbdoc_review.pipeline.navigation_merge import (
    extra_toc_hrefs_from_md_targets,
    run_navigation_merges,
    run_navigation_verifies,
)
from ydbdoc_review.pipeline.orchestrator import run_pr_translation
from ydbdoc_review.pipeline.pairs import (
    build_navigation_pairs,
    build_verify_navigation_pairs,
)
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    build_commit_message,
    build_full_report,
    build_source_pr_comment,
    build_translate_handoff_comment,
    build_translation_pr_body,
    build_verify_fixup_pr_body,
    build_verify_fixup_source_comment,
)
from ydbdoc_review.reporting.locations import ReportLinkContext
from ydbdoc_review.translation.glossary import Glossary, load_glossary

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
        raise GitHubConfigError(
            "GitHub token not configured. Set GITHUB_TOKEN."
        )
    if not push:
        raise GitHubConfigError(
            "GitHub push token not configured. Set GITHUB_PUSH_TOKEN or GITHUB_TOKEN."
        )
    return api, push


def _next_report_number(
    client: GitHubClient, owner: str, repo: str, issue_number: int
) -> int:
    count = 0
    for comment in client.iter_issue_comments(owner, repo, issue_number):
        body = str(comment.get("body") or "")
        if _REPORT_MARKER in body:
            count += 1
    return count + 1


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


def _apply_results_to_disk(
    repo_path: str, result: PRTranslationResult, *, dry_run: bool
) -> TouchedPaths:
    """Write translated markdown, navigation YAML, and deletes; return paths."""
    written: list[str] = []
    deleted: list[str] = []
    for run in result.pair_results:
        if run.skipped or run.error:
            continue
        rel = run.plan.target_path
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
    return TouchedPaths(written=written, deleted=deleted)


def _run_verify_pairs(
    contents: list[PairContent],
    client: YandexLLMClient,
    glossary: Glossary,
    config: Config,
) -> PRTranslationResult:
    """Critic-only QA for existing RU/EN pairs."""
    state = PRRunState(contents=contents)
    ctx = PRHarnessContext.from_options(client, glossary=glossary, config=config)
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
) -> DocJobResult:
    """Full ``doc_translate`` workflow for a source PR."""
    started = time.monotonic()
    cfg = config or load_config()
    api_token, push_token = _github_tokens(cfg)
    owner, repo = parse_repo(github_repo)
    gh = GitHubClient(api_token)

    ctx = pull_request_context(gh, owner, repo, pr_number)
    branch = f"{cfg.paths.translation_branch_prefix}{pr_number}"
    upstream_url = repo_https_clone_url(owner, repo)
    branch_remote_url, branch_start_ref = translation_branch_base(ctx)

    changes = list_pr_file_changes_git(repo_path, merge_base_with)
    pairs = build_pairs_from_changes(changes, docs_root=cfg.paths.docs_root)
    nav_pairs = build_navigation_pairs(changes, docs_root=cfg.paths.docs_root)
    job = DocJobResult(
        mode="doc_translate",
        pr_number=pr_number,
        source_pr_number=pr_number,
        translation_branch=branch,
        dry_run=dry_run,
    )
    if not pairs and not nav_pairs:
        logger.info("No doc or navigation pairs in PR #%s", pr_number)
        return job

    cfg.secrets.require_yandex()
    client = YandexLLMClient.from_config(cfg)
    glossary = load_glossary()

    if pairs:
        contents = load_pair_contents(
            repo_path, pairs, merge_base_with=merge_base_with
        )
        pr_result = run_pr_translation(
            contents,
            client,
            glossary,
            config=cfg,
            use_analyze_llm=False,
        )
    else:
        pr_result = PRTranslationResult()

    if nav_pairs:
        md_en_paths = {
            r.plan.target_path
            for r in pr_result.pair_results
            if r.target_text is not None and not r.error
        }
        pr_result.navigation_results = run_navigation_merges(
            nav_pairs,
            repo_path=repo_path,
            merge_base_with=merge_base_with,
            client=client,
            glossary=glossary,
            config=cfg,
            extra_toc_hrefs=extra_toc_hrefs_from_md_targets(md_en_paths),
        )

    pr_result.completeness_gaps = completeness_gaps(
        changes, pr_result, docs_root=cfg.paths.docs_root
    )
    job.pr_result = pr_result

    touched = _apply_results_to_disk(repo_path, pr_result, dry_run=dry_run)

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
            )
            pushed = True
    job.committed = committed
    job.pushed = pushed

    elapsed = time.monotonic() - started
    meta = ReportMeta(mode="doc_translate", report_number=1, elapsed_s=elapsed)

    if dry_run:
        return job

    tr_pr_number: int | None = None
    tr_pr_url: str | None = None
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
                    gh.add_issue_labels(
                        owner, repo, tr_pr_number, ["documentation"]
                    )
                except GitHubAPIError as exc:
                    logger.warning(
                        "Could not add documentation label to PR #%s: %s",
                        tr_pr_number,
                        exc,
                    )
            if pushed:
                try:
                    gh.add_issue_labels(
                        owner, repo, tr_pr_number, ["doc_verify"]
                    )
                    logger.info(
                        "Added doc_verify label to translation PR #%s",
                        tr_pr_number,
                    )
                except GitHubAPIError as exc:
                    logger.warning(
                        "Could not add doc_verify label to PR #%s: %s "
                        "(add manually or use trigger-verify-ci with YDBOT_TOKEN)",
                        tr_pr_number,
                        exc,
                    )

    if tr_pr_number is not None:
        report_num = _next_report_number(gh, owner, repo, tr_pr_number)
        report_meta = ReportMeta(
            mode="doc_translate",
            report_number=report_num,
            elapsed_s=elapsed,
            checkout_ref=git_head_sha(repo_path),
        )
        job.translation_comment_url = _safe_post_issue_comment(
            gh,
            owner,
            repo,
            tr_pr_number,
            build_translate_handoff_comment(
                pr_result,
                source_pr=pr_number,
                source_repo=github_repo,
                meta=report_meta,
                config=cfg,
                usage=client.usage_tracker,
            ),
            label="translation handoff",
        )

    job.source_comment_url = _safe_post_issue_comment(
        gh,
        owner,
        repo,
        pr_number,
        build_source_pr_comment(
            pr_result,
            translation_pr_number=tr_pr_number,
            meta=meta,
            config=cfg,
            usage=client.usage_tracker,
        ),
        label="source PR summary",
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
) -> DocJobResult:
    """``doc_verify`` workflow on a translation PR."""
    started = time.monotonic()
    cfg = config or load_config()
    api_token, push_token = _github_tokens(cfg)
    owner, repo = parse_repo(github_repo)
    gh = GitHubClient(api_token)

    ctx = pull_request_context(gh, owner, repo, pr_number)
    source_pr = source_pr_number_from_branch(
        ctx.head_ref, prefix=cfg.paths.translation_branch_prefix
    )
    if source_pr is None:
        pull_body = str(
            gh.get_pull(owner, repo, pr_number).get("body") or ""
        )
        source_pr = parse_source_pr_from_text(
            f"{ctx.title}\n{pull_body}"
        )
    upstream_url = repo_https_clone_url(owner, repo)
    fixup_source_pr = source_pr or pr_number
    fixup_branch = verify_fixup_branch(
        cfg.paths.verify_fixup_branch_prefix, fixup_source_pr
    )
    fixup_base_ref, fixup_base_branch = translation_branch_base(ctx)
    fixup_pr_base = verify_fixup_pr_base(
        ctx, translation_branch_prefix=cfg.paths.translation_branch_prefix
    )

    changes = list_pr_file_changes_git(repo_path, merge_base_with)
    pairs = build_pairs_from_changes(changes, docs_root=cfg.paths.docs_root)
    source_changes = (
        list_pr_file_changes_api(gh, owner, repo, source_pr)
        if source_pr is not None
        else None
    )
    nav_pairs = build_verify_navigation_pairs(
        changes,
        docs_root=cfg.paths.docs_root,
        source_changes=source_changes,
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
        logger.info("No doc or navigation pairs for verify on PR #%s", pr_number)
        return job

    cfg.secrets.require_yandex()
    client = YandexLLMClient.from_config(cfg)
    glossary = load_glossary()

    if pairs:
        if source_pr is None:
            logger.warning(
                "doc_verify PR #%s: source PR unknown — RU from checkout (may differ from doc_translate)",
                pr_number,
            )
            contents = load_pair_contents(
                repo_path, pairs, merge_base_with=merge_base_with
            )
        else:
            contents = load_verify_pair_contents(
                repo_path,
                pairs,
                merge_base_with=merge_base_with,
                gh=gh,
                owner=owner,
                repo=repo,
                source_pr=source_pr,
            )
        pr_result = _run_verify_pairs(contents, client, glossary, cfg)
    else:
        pr_result = PRTranslationResult()

    if nav_pairs:
        if source_pr is not None:
            ru_nav_texts = load_verify_navigation_ru_texts(
                nav_pairs,
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

        md_en_paths = {p.en_path for p in pairs if p.en_changed}
        pr_result.navigation_results = run_navigation_verifies(
            nav_pairs,
            repo_path=repo_path,
            merge_base_with=merge_base_with,
            ru_pr_by_path=ru_nav_texts,
            extra_toc_hrefs=extra_toc_hrefs_from_md_targets(md_en_paths),
        )

    job.pr_result = pr_result

    touched = _apply_results_to_disk(repo_path, pr_result, dry_run=dry_run)

    committed = pushed = False
    fixup_pr_number: int | None = None
    fixup_pr_url: str | None = None
    if touched and not dry_run and not no_commit:
        msg = build_commit_message(
            fixup_source_pr,
            pr_result,
            config=cfg,
            verify=True,
        )
        prepare_translation_branch_on_base(
            repo_path,
            translation_branch=fixup_branch,
            base_remote_url=fixup_base_ref,
            base_remote_name="ydbdoc-review-upstream",
            base_branch=fixup_base_branch,
            paths=touched.written,
            deleted_paths=touched.deleted,
        )
        committed = git_commit_paths(
            repo_path,
            touched.written,
            msg,
            _GITHUB_ACTOR_NAME,
            _GITHUB_ACTOR_EMAIL,
            deleted_paths=touched.deleted,
        )
        if committed:
            if gh.delete_branch(owner, repo, fixup_branch):
                logger.info(
                    "Deleted stale doc_verify fixup branch %s before push",
                    fixup_branch,
                )
            logger.info(
                "Pushing doc_verify fixup branch %s to upstream (verified PR #%s head: %s)",
                fixup_branch,
                pr_number,
                ctx.head_repo_full_name,
            )
            push_branch(
                repo_path,
                "ydbdoc-review-push",
                fixup_branch,
                push_token,
                upstream_url,
            )
            pushed = True
    job.committed = committed
    job.pushed = pushed

    elapsed = time.monotonic() - started
    if dry_run:
        return job

    if pushed and fixup_branch is not None:
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
                    gh.add_issue_labels(
                        owner, repo, fixup_pr_number, ["documentation"]
                    )
                except GitHubAPIError as exc:
                    logger.warning(
                        "Could not add documentation label to PR #%s: %s",
                        fixup_pr_number,
                        exc,
                    )

    report_num = _next_report_number(gh, owner, repo, pr_number)
    meta = ReportMeta(
        mode="doc_verify",
        report_number=report_num,
        elapsed_s=elapsed,
        checkout_ref=git_head_sha(repo_path),
    )
    job.translation_comment_url = _safe_post_issue_comment(
        gh,
        owner,
        repo,
        pr_number,
        build_full_report(
            pr_result,
            meta=meta,
            config=cfg,
            usage=client.usage_tracker,
            glossary=glossary,
            link=ReportLinkContext(github_repo=github_repo, ref=ctx.head_ref),
        ),
        label="doc_verify QA report",
    )
    if fixup_pr_number is not None:
        job.source_comment_url = _safe_post_issue_comment(
            gh,
            owner,
            repo,
            pr_number,
            build_verify_fixup_source_comment(fixup_pr_number),
            label="doc_verify fixup link",
        )
    return job
