"""GitHub Actions workflow: doc_translate and doc_verify."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from ydbdoc_review.config.loader import Config, load_config
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.github.errors import GitHubConfigError
from ydbdoc_review.github.git_ops import (
    git_commit_paths,
    prepare_translation_branch_on_base,
    push_branch,
    write_text,
)
from ydbdoc_review.github.pr import (
    build_pairs_from_changes,
    list_pr_file_changes_git,
    load_pair_contents,
    parse_repo,
    parse_source_pr_from_text,
    pull_request_context,
    repo_https_clone_url,
    source_pr_number_from_branch,
    translation_branch_base,
    translation_pr_base,
)
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMError
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.orchestrator import run_pr_translation
from ydbdoc_review.pipeline.translate_file import translate_file
from ydbdoc_review.pipeline.types import PRTranslationResult, PairRunResult
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    build_commit_message,
    build_full_report,
    build_source_pr_comment,
    build_translation_pr_body,
)
from ydbdoc_review.reporting.locations import ReportLinkContext
from ydbdoc_review.translation.glossary import Glossary, load_glossary

logger = logging.getLogger(__name__)

_GITHUB_ACTOR_NAME = "github-actions[bot]"
_GITHUB_ACTOR_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
_REPORT_MARKER = "ydbdoc-review — отчёт"


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


def _apply_results_to_disk(
    repo_path: str, result: PRTranslationResult, *, dry_run: bool
) -> list[str]:
    """Write translated / delete mirrored files; return affected paths."""
    touched: list[str] = []
    for run in result.pair_results:
        if run.skipped or run.error:
            continue
        rel = run.plan.target_path
        if run.deleted:
            touched.append(rel)
            if dry_run:
                continue
            path = Path(repo_path) / rel.replace("/", os.sep)
            if path.is_file():
                path.unlink()
            continue
        if run.target_text is None:
            continue
        touched.append(rel)
        if not dry_run:
            write_text(repo_path, rel, run.target_text)
    return touched


def _run_verify_pairs(
    contents: list[PairContent],
    client: YandexLLMClient,
    glossary: Glossary,
    config: Config,
) -> PRTranslationResult:
    """Critic-only QA for existing RU/EN pairs."""
    results: list[PairRunResult] = []
    for content in contents:
        pair = content.pair
        if not content.ru_text or not content.en_text:
            plan = PairPlan(
                pair=pair,
                action="skip",
                source_path=pair.ru_path,
                target_path=pair.en_path,
                source_lang="ru",
                target_lang="en",
                summary="verify skip — missing RU or EN text",
            )
            results.append(PairRunResult(plan=plan, skipped=True))
            continue
        plan = PairPlan(
            pair=pair,
            action="critic_only",
            source_path=pair.ru_path,
            target_path=pair.en_path,
            source_lang="ru",
            target_lang="en",
            summary="doc_verify critic pass",
        )
        try:
            file_result = translate_file(
                content.ru_text,
                client,
                glossary,
                file_path=pair.ru_path,
                config=config,
                source_lang="ru",
                target_lang="en",
                enable_translate=False,
                existing_target_text=content.en_text,
                enable_critic=True,
            )
        except (LLMError, ValueError) as exc:
            logger.exception("Verify failed for %s", pair.en_path)
            results.append(PairRunResult(plan=plan, error=str(exc)))
            continue
        results.append(
            PairRunResult(
                plan=plan,
                target_text=file_result.final_text,
                file_result=file_result,
            )
        )
    return PRTranslationResult(pair_results=results)


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
    job = DocJobResult(
        mode="doc_translate",
        pr_number=pr_number,
        source_pr_number=pr_number,
        translation_branch=branch,
        dry_run=dry_run,
    )
    if not pairs:
        logger.info("No doc pairs in PR #%s", pr_number)
        return job

    contents = load_pair_contents(
        repo_path, pairs, merge_base_with=merge_base_with
    )
    cfg.secrets.require_yandex()
    client = YandexLLMClient.from_config(cfg)
    glossary = load_glossary()

    pr_result = run_pr_translation(
        contents,
        client,
        glossary,
        config=cfg,
        use_analyze_llm=True,
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
            paths=touched,
        )
        msg = build_commit_message(pr_number, pr_result, config=cfg)
        committed = git_commit_paths(
            repo_path,
            touched,
            msg,
            _GITHUB_ACTOR_NAME,
            _GITHUB_ACTOR_EMAIL,
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
            tr_pr_url, tr_pr_number = opened
            job.translation_pr_url = tr_pr_url
            job.translation_pr_number = tr_pr_number

    job.source_comment_url = gh.post_issue_comment(
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
    )

    if tr_pr_number is not None:
        report_num = _next_report_number(gh, owner, repo, tr_pr_number)
        meta = ReportMeta(
            mode="doc_translate",
            report_number=report_num,
            elapsed_s=elapsed,
        )
        job.translation_comment_url = gh.post_issue_comment(
            owner,
            repo,
            tr_pr_number,
            build_full_report(
                pr_result,
                meta=meta,
                config=cfg,
                usage=client.usage_tracker,
                glossary=glossary,
                link=ReportLinkContext(github_repo=github_repo, ref=branch),
            ),
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
    upstream_url = repo_https_clone_url(owner, repo)
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

    changes = list_pr_file_changes_git(repo_path, merge_base_with)
    pairs = build_pairs_from_changes(changes, docs_root=cfg.paths.docs_root)
    job = DocJobResult(
        mode="doc_verify",
        pr_number=pr_number,
        source_pr_number=source_pr,
        translation_branch=ctx.head_ref,
        translation_pr_number=pr_number,
        dry_run=dry_run,
    )
    if not pairs:
        logger.info("No doc pairs for verify on PR #%s", pr_number)
        return job

    contents = load_pair_contents(
        repo_path, pairs, merge_base_with=merge_base_with
    )
    cfg.secrets.require_yandex()
    client = YandexLLMClient.from_config(cfg)
    glossary = load_glossary()

    pr_result = _run_verify_pairs(contents, client, glossary, cfg)
    job.pr_result = pr_result

    touched = _apply_results_to_disk(repo_path, pr_result, dry_run=dry_run)

    committed = pushed = False
    if touched and not dry_run and not no_commit:
        msg = build_commit_message(
            source_pr or pr_number,
            pr_result,
            config=cfg,
            verify=True,
        )
        committed = git_commit_paths(
            repo_path,
            touched,
            msg,
            _GITHUB_ACTOR_NAME,
            _GITHUB_ACTOR_EMAIL,
        )
        if committed:
            push_branch(
                repo_path,
                "ydbdoc-review-push",
                ctx.head_ref,
                push_token,
                upstream_url,
            )
            pushed = True
    job.committed = committed
    job.pushed = pushed

    elapsed = time.monotonic() - started
    if dry_run:
        return job

    report_num = _next_report_number(gh, owner, repo, pr_number)
    meta = ReportMeta(
        mode="doc_verify",
        report_number=report_num,
        elapsed_s=elapsed,
    )
    job.translation_comment_url = gh.post_issue_comment(
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
    )
    return job
