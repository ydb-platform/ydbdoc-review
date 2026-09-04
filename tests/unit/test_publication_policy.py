"""Top-level publication contract for paid doc_translate candidates."""

from __future__ import annotations

import subprocess
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.errors import GitHubAPIError
from ydbdoc_review.github.workflow import (
    job_requires_nonzero_exit,
    run_doc_translate,
    run_doc_verify,
)
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    FinalTreeBlocker,
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    build_full_report,
    build_translation_pr_body,
)
from ydbdoc_review.translation.manual import ManualAction
from ydbdoc_review.validation.link_contract import LinkContractIssue


def _env() -> dict[str, str]:
    return {
        "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
        "YDBDOC_YC_FOLDER_ID": "b1",
        "YDBDOC_YC_API_KEY": "k",
        "GITHUB_TOKEN": "gh",
        "GITHUB_PUSH_TOKEN": "ghp",
        "YDBDOC_SKIP_OPS_GATES": "1",
    }


@pytest.fixture
def publication_repo(tmp_path: Path) -> str:
    """Real git tree used by production scope and final-tree readers."""
    repo = tmp_path / "repo"
    ru = repo / "ydb" / "docs" / "ru"
    en = repo / "ydb" / "docs" / "en"
    en_core = en / "core"
    ru.mkdir(parents=True)
    en_core.mkdir(parents=True)
    (ru / "a.md").write_text("Привет.\n", encoding="utf-8")
    (en / "a.md").write_text("Hello.\n", encoding="utf-8")
    (en_core / "toc_p.yaml").write_text(
        "items:\n- name: A\n  href: ../a.md\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    return str(repo)


def _pair_result(
    *,
    target_text: str | None = "Translated.\n",
    error: str | None = None,
    warnings: list[str] | None = None,
    blocking: list[str] | None = None,
    manual_actions: list[ManualAction] | None = None,
    alignment_error: str | None = None,
    link_issues: tuple[LinkContractIssue, ...] = (),
) -> PRTranslationResult:
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    file_result = None
    if target_text is not None:
        file_result = FileTranslationResult(
            file_path=pair.en_path,
            final_text=target_text,
            segments_count=1,
            verdict="blocked" if alignment_error or link_issues else "ok",
            prompt_version="v1",
            heuristic_warnings=list(warnings or ()),
            heuristic_blocking=list(blocking or ()),
            manual_actions=list(manual_actions or ()),
            segment_alignment_error=alignment_error,
            link_contract_issues=link_issues,
        )
    return PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                target_text=target_text,
                file_result=file_result,
                error=error,
                source_text="Привет.\n",
                validation_issues=link_issues,
            )
        ]
    )


def _run_top_level(
    repo_path: str,
    pr_result: PRTranslationResult,
    *,
    existing_pr: bool = False,
    late_existing_pr: bool = False,
    remote_branch_exists: bool | None = None,
    impact_path: str | None = None,
    create_succeeds: bool = True,
    source_changes: list[tuple[str, str]] | None = None,
    event_log: list[str] | None = None,
    draft_conversion_fails: bool = False,
    prepush_create_returns_none: bool = False,
    real_push_remote: str | None = None,
    remote_branch_sha: str | None = None,
    push_fails: bool = False,
):
    pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    gh = MagicMock()
    gh.get_pull.return_value = pull
    gh.iter_issue_comments.return_value = iter(())
    branch_was_present = (
        late_existing_pr if remote_branch_exists is None else remote_branch_exists
    )
    gh.get_branch_sha.return_value = (
        remote_branch_sha or "old-remote-sha" if branch_was_present else None
    )
    created_pull = (
        (
            "https://github.com/o/r/pull/99",
            99,
            not existing_pr and not late_existing_pr,
        )
        if create_succeeds
        else None
    )
    gh.find_open_pull_by_head.side_effect = lambda *_args, **_kwargs: (
        event_log.append("discover") if event_log is not None else None
    ) or (("https://github.com/o/r/pull/99", 99) if existing_pr else None)
    create_calls = 0

    def _create(*_args, **_kwargs):
        nonlocal create_calls
        create_calls += 1
        if event_log is not None:
            event_log.append("create")
        if prepush_create_returns_none and create_calls == 1:
            return None
        return created_pull

    gh.create_pull.side_effect = _create
    gh.update_pull_body.side_effect = lambda *_args, **_kwargs: (
        event_log.append("body") if event_log is not None else None
    )

    def _convert(*_args, **_kwargs):
        if event_log is not None:
            event_log.append("draft")
        if draft_conversion_fails:
            raise GitHubAPIError("cannot convert", status_code=403)
        return True

    gh.convert_pull_to_draft.side_effect = _convert
    gh.post_issue_comment.return_value = "https://github.com/o/r/pull/7#issuecomment-1"
    ops_ctx = SimpleNamespace(recorder=None, continue_feedback=None)

    with ExitStack() as stack:
        stack.enter_context(patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh))
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.begin_ops_job",
                return_value=(ops_ctx, GateResult(ok=True), None),
            )
        )
        finish = stack.enter_context(patch("ydbdoc_review.github.workflow.finish_ops_job"))
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.list_pr_file_changes_git",
                return_value=(
                    [("ydb/docs/ru/a.md", "modified")]
                    if source_changes is None
                    else source_changes
                ),
            )
        )
        stack.enter_context(
            patch("ydbdoc_review.github.workflow.list_pr_file_changes_api", return_value=[])
        )
        stack.enter_context(
            patch("ydbdoc_review.github.workflow.run_pr_translation", return_value=pr_result)
        )
        stack.enter_context(
            patch("ydbdoc_review.github.workflow.apply_orphan_toc_page_checks", return_value=[])
        )
        if impact_path is not None:
            stack.enter_context(
                patch(
                    "ydbdoc_review.github.workflow._declare_exact_ascii_fragment_targets_after_apply",
                    return_value=[impact_path],
                )
            )
        prepare = stack.enter_context(
            patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base")
        )
        commit = stack.enter_context(
            patch("ydbdoc_review.github.workflow.git_commit_paths", return_value=True)
        )
        if real_push_remote is None:
            push = stack.enter_context(patch("ydbdoc_review.github.workflow.push_branch"))
            stack.enter_context(
                patch("ydbdoc_review.github.workflow.rollback_pushed_branch")
            )
            if event_log is not None:
                def _push_mock(*_args, **_kwargs):
                    event_log.append("push")
                    if push_fails:
                        raise RuntimeError("guarded push lease failed")

                push.side_effect = _push_mock
            elif push_fails:
                push.side_effect = RuntimeError("guarded push lease failed")
        else:
            import ydbdoc_review.github.workflow as workflow

            real_push = workflow.push_branch

            def _push_real(*args, **kwargs):
                if event_log is not None:
                    event_log.append("push")
                return real_push(*args, **kwargs)

            stack.enter_context(
                patch(
                    "ydbdoc_review.github.git_ops.remote_push_url",
                    return_value=real_push_remote,
                )
            )
            push = stack.enter_context(
                patch(
                    "ydbdoc_review.github.workflow.push_branch",
                    side_effect=_push_real,
                )
            )
        stack.enter_context(
            patch(
                "ydbdoc_review.github.workflow.run_doc_verify",
                return_value=SimpleNamespace(
                    translation_comment_url="https://github.com/o/r/pull/99#issuecomment-2",
                    pr_result=pr_result,
                ),
            )
        )
        job = run_doc_translate(
            repo_path=repo_path,
            github_repo="o/r",
            pr_number=7,
            merge_base_with="HEAD",
            config=load_config(env=_env()),
        )
    return job, gh, prepare, commit, push, finish


def test_safe_final_link_blocker_publishes_draft_red(publication_repo: str):
    result = _pair_result(target_text="See [missing](missing.md).\n")

    job, gh, prepare, commit, push, finish = _run_top_level(publication_repo, result)

    assert job.pr_result.completeness_gaps == []
    assert getattr(job.pr_result, "publication_impact", None) == "PUBLISH_RED"
    blockers = getattr(job.pr_result, "final_tree_blockers", ())
    assert [(b.path, b.code) for b in blockers] == [
        ("ydb/docs/en/a.md", "en_link_target")
    ]
    prepare.assert_called_once()
    commit.assert_called_once()
    push.assert_called_once()
    assert push.call_args.kwargs["guard_remote_ref"] is True
    assert push.call_args.kwargs["expected_remote_sha"] is None
    gh.create_pull.assert_called_once()
    assert gh.create_pull.call_args.kwargs["draft"] is True
    pr_body = gh.create_pull.call_args.kwargs["body"]
    assert "QA RED, do not merge" in pr_body
    assert "missing.md" in pr_body
    source_summary = gh.post_issue_comment.call_args.args[3]
    assert "published_red" in source_summary
    assert "🔴" in source_summary and "не мержить" in source_summary
    full_report = build_full_report(
        job.pr_result,
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=load_config(env=_env()),
    )
    assert "QA RED, do not merge" in full_report
    assert "missing.md" in full_report
    assert finish.call_args.kwargs["status"] == "published_red"
    assert job_requires_nonzero_exit(job) is False


def test_missing_red_pr_is_hard_failure_and_not_false_green(publication_repo: str):
    result = _pair_result(target_text="See [missing](missing.md).\n")

    job, gh, _prepare, _commit, _push, finish = _run_top_level(
        publication_repo,
        result,
        create_succeeds=False,
    )

    assert job.translation_pr_number is None
    assert job_requires_nonzero_exit(job) is True
    source_summary = gh.post_issue_comment.call_args.args[3]
    assert "translation PR **не создан**" in source_summary
    assert "🔴" in source_summary
    assert "перевод готов" not in source_summary
    assert finish.call_args.kwargs["status"] == "failed"


def test_final_tree_blocker_without_pair_survives_as_draft_red(publication_repo: str):
    impact_path = "ydb/docs/en/impact.md"
    Path(publication_repo, impact_path).write_text(
        "Redirected page links to [missing](gone.md).\n",
        encoding="utf-8",
    )
    result = _pair_result()

    job, gh, prepare, commit, push, _finish = _run_top_level(
        publication_repo,
        result,
        impact_path=impact_path,
    )

    assert all(run.plan.target_path != impact_path for run in job.pr_result.pair_results)
    blockers = getattr(job.pr_result, "final_tree_blockers", ())
    assert [(b.path, b.code) for b in blockers] == [(impact_path, "en_link_target")]
    assert getattr(job.pr_result, "publication_impact", None) == "PUBLISH_RED"
    prepare.assert_called_once()
    commit.assert_called_once()
    push.assert_called_once()
    assert gh.create_pull.call_args.kwargs["draft"] is True
    assert job_requires_nonzero_exit(job) is False


def test_real_git_commit_preserves_impact_blocker_through_inline_verify(
    publication_repo: str,
    tmp_path: Path,
):
    import ydbdoc_review.github.workflow as workflow

    impact_path = "ydb/docs/en/impact.md"
    Path(publication_repo, impact_path).write_text(
        "Existing impact page.\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", impact_path],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "add existing impact page"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    upstream = tmp_path / "upstream.git"
    subprocess.run(
        ["git", "clone", "--bare", publication_repo, str(upstream)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(upstream)],
        cwd=publication_repo,
        check=True,
    )
    subprocess.run(
        ["git", "fetch", "origin", "main:refs/remotes/origin/feature/docs"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    upstream_base_sha = subprocess.run(
        ["git", "--git-dir", str(upstream), "rev-parse", "refs/heads/main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    Path(publication_repo, "source-checkout-only.txt").write_text(
        "must not become the translation base\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "source-checkout-only.txt"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "source checkout diverges from upstream"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    source_checkout_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert source_checkout_sha != upstream_base_sha
    Path(publication_repo, impact_path).write_text(
        "See [missing](gone.md).\n",
        encoding="utf-8",
    )
    translate_result = _pair_result(target_text="Translated.\n")
    verify_result = _pair_result(target_text="Translated.\n")

    source_pull = {
        "title": "docs",
        "head": {
            "ref": "feature/docs",
            "sha": "source-head",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": "source-base"},
    }
    translation_pull = {
        "title": "Auto-translate docs from PR #7",
        "body": "",
        "head": {
            "ref": "ydbdoc-review/pr-7",
            "sha": "translation-head",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    gh = MagicMock()
    gh.get_pull.side_effect = lambda _o, _r, number: (
        translation_pull if number == 99 else source_pull
    )
    gh.get_file_text.return_value = "Привет.\n"
    gh.iter_issue_comments.return_value = iter(())
    gh.find_open_pull_by_head.return_value = None
    gh.create_pull.return_value = ("https://github.com/o/r/pull/99", 99, True)
    gh.post_issue_comment.return_value = "comment-url"

    translate_changes = [("ydb/docs/ru/a.md", "modified")]
    verify_changes = [
        ("ydb/docs/en/a.md", "modified"),
        (impact_path, "modified"),
    ]
    git_change_calls = iter((translate_changes, verify_changes, verify_changes))

    def _api_changes(_gh, _owner, _repo, number):
        return translate_changes if number == 7 else verify_changes

    real_prepare = workflow.prepare_translation_branch_on_base
    prepare_calls = 0

    def _prepare_once(*args, **kwargs):
        nonlocal prepare_calls
        prepare_calls += 1
        if prepare_calls == 1:
            return real_prepare(*args, **kwargs)
        return None

    real_verify = workflow.run_doc_verify
    verify_jobs = []

    def _capture_real_verify(**kwargs):
        verify_job = real_verify(**kwargs)
        verify_jobs.append(verify_job)
        return verify_job

    ops_ctx = SimpleNamespace(recorder=None, continue_feedback=None)
    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh),
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(ops_ctx, GateResult(ok=True), None),
        ),
        patch("ydbdoc_review.github.workflow.finish_ops_job"),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
            side_effect=lambda *_args, **_kwargs: next(git_change_calls),
        ),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            side_effect=_api_changes,
        ),
        patch(
            "ydbdoc_review.github.workflow.run_pr_translation",
            return_value=translate_result,
        ),
        patch(
            "ydbdoc_review.github.workflow._run_verify_pairs",
            return_value=verify_result,
        ),
        patch(
            "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks",
            return_value=[],
        ),
        patch(
            "ydbdoc_review.github.workflow._declare_exact_ascii_fragment_targets_after_apply",
            return_value=[impact_path],
        ),
        patch(
            "ydbdoc_review.github.workflow.translation_branch_base",
            return_value=(str(upstream), "main"),
        ),
        patch(
            "ydbdoc_review.github.workflow.prepare_translation_branch_on_base",
            side_effect=_prepare_once,
        ),
        patch("ydbdoc_review.github.workflow.push_branch"),
        patch(
            "ydbdoc_review.github.workflow.run_doc_verify",
            side_effect=_capture_real_verify,
        ),
    ):
        job = run_doc_translate(
            repo_path=publication_repo,
            github_repo="o/r",
            pr_number=7,
            merge_base_with="HEAD",
            config=load_config(env=_env()),
        )

    committed_impact = subprocess.run(
        ["git", "show", f"HEAD:{impact_path}"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    checked_out_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    translation_parent_sha = subprocess.run(
        ["git", "rev-parse", "HEAD^"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert committed_impact == "See [missing](gone.md).\n"
    assert checked_out_branch == "ydbdoc-review/pr-7"
    assert translation_parent_sha == upstream_base_sha
    assert translation_parent_sha != source_checkout_sha
    assert job.committed is True
    assert verify_jobs
    assert [(b.path, b.code) for b in verify_jobs[-1].pr_result.final_tree_blockers] == [
        (impact_path, "en_link_target")
    ]
    assert verify_jobs[-1].pr_result.publication_impact == "PUBLISH_RED"


def test_verify_empty_scoped_result_preserves_inherited_no_pair_blocker(
    publication_repo: str,
):
    blocker = FinalTreeBlocker(
        path="ydb/docs/en/impact.md",
        code="en_link_target",
        message="en_link_target: impact.md: missing target",
    )
    pull = {
        "title": "Auto-translate docs from PR #7",
        "body": "",
        "head": {
            "ref": "ydbdoc-review/pr-7",
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    gh = MagicMock()
    gh.get_pull.return_value = pull

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh),
        patch("ydbdoc_review.github.workflow.list_pr_file_changes_git", return_value=[]),
        patch("ydbdoc_review.github.workflow.list_pr_file_changes_api", return_value=[]),
    ):
        job = run_doc_verify(
            repo_path=publication_repo,
            github_repo="o/r",
            pr_number=99,
            merge_base_with="HEAD",
            dry_run=True,
            config=load_config(env=_env()),
            inherited_final_tree_blockers=[blocker],
            skip_ops_gates=True,
        )

    assert job.pr_result.final_tree_blockers == [blocker]
    assert job.pr_result.publication_impact == "PUBLISH_RED"


def test_standalone_verify_rescans_durable_no_pair_blocker_outside_source_scope(
    publication_repo: str,
):
    impact_path = "ydb/docs/en/impact.md"
    Path(publication_repo, impact_path).write_text(
        "See [missing](gone.md).\n",
        encoding="utf-8",
    )
    blocker = FinalTreeBlocker(
        path=impact_path,
        code="en_link_target",
        message="en_link_target: impact.md: missing target `gone.md`",
    )
    published_result = _pair_result()
    published_result.final_tree_blockers = [blocker]
    published_result.publication_impact = PublicationImpact.PUBLISH_RED
    body = build_translation_pr_body(
        7,
        "o/r",
        publication_result=published_result,
    )
    assert "ydbdoc-final-tree-blockers:v1" in body

    translation_pull = {
        "title": "Auto-translate docs from PR #7",
        "body": body,
        "head": {
            "ref": "ydbdoc-review/pr-7",
            "sha": "translation-sha",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    source_pull = {
        "title": "docs",
        "body": "",
        "head": {
            "ref": "docs",
            "sha": "source-head",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": "source-base"},
    }
    gh = MagicMock()
    gh.get_pull.side_effect = lambda _o, _r, number: (
        translation_pull if number == 99 else source_pull
    )
    gh.get_file_text.return_value = "Привет.\n"

    def _api_changes(_gh, _owner, _repo, number):
        if number == 7:
            return [("ydb/docs/ru/a.md", "modified")]
        return [("ydb/docs/en/a.md", "modified"), (impact_path, "modified")]

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
            return_value=[("ydb/docs/en/a.md", "modified"), (impact_path, "modified")],
        ),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            side_effect=_api_changes,
        ),
        patch(
            "ydbdoc_review.github.workflow._run_verify_pairs",
            return_value=_pair_result(),
        ),
        patch(
            "ydbdoc_review.github.workflow.apply_orphan_toc_page_checks",
            return_value=[],
        ),
    ):
        job = run_doc_verify(
            repo_path=publication_repo,
            github_repo="o/r",
            pr_number=99,
            merge_base_with="HEAD",
            dry_run=True,
            config=load_config(env=_env()),
            skip_ops_gates=True,
        )

    assert [(item.path, item.code) for item in job.pr_result.final_tree_blockers] == [
        (impact_path, "en_link_target")
    ]
    assert job.pr_result.final_tree_blockers != [blocker]
    assert "missing file" in job.pr_result.final_tree_blockers[0].message
    assert job.pr_result.publication_impact == "PUBLISH_RED"


def test_verify_critic_fix_recursion_preserves_inherited_no_pair_blocker(
    publication_repo: str,
):
    blocker = FinalTreeBlocker(
        path="ydb/docs/en/impact.md",
        code="en_link_target",
        message="en_link_target: impact.md: missing target",
    )
    translation_pull = {
        "title": "Auto-translate docs from PR #7",
        "body": "",
        "head": {
            "ref": "ydbdoc-review/pr-7",
            "sha": "translation-sha",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main"},
    }
    source_pull = {
        "title": "docs",
        "head": {
            "ref": "docs",
            "sha": "source-head",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": "source-base"},
    }
    gh = MagicMock()
    gh.get_pull.side_effect = lambda _o, _r, number: (
        translation_pull if number == 99 else source_pull
    )
    gh.get_file_text.return_value = "Привет.\n"
    gh.iter_issue_comments.return_value = iter(())
    gh.post_issue_comment.return_value = "comment-url"

    def _api_changes(_gh, _owner, _repo, number):
        if number == 7:
            return [("ydb/docs/ru/a.md", "modified")]
        return [("ydb/docs/en/a.md", "modified")]

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_git",
            return_value=[("ydb/docs/en/a.md", "modified")],
        ),
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            side_effect=_api_changes,
        ),
        patch(
            "ydbdoc_review.github.workflow._run_verify_pairs",
            side_effect=[
                _pair_result(target_text="Critic fix one.\n"),
                _pair_result(target_text="Critic fix two.\n"),
            ],
        ),
        patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base"),
        patch(
            "ydbdoc_review.github.workflow.git_commit_paths",
            side_effect=[True, False],
        ),
        patch("ydbdoc_review.github.workflow.push_branch"),
        patch(
            "ydbdoc_review.github.workflow.git_head_sha",
            side_effect=[
                "checkout-one",
                "before-one",
                "after-one",
                "checkout-two",
                "before-two",
            ],
        ),
        patch(
            "ydbdoc_review.github.workflow._enforce_report_checkout_bytes",
            return_value=[],
        ),
    ):
        job = run_doc_verify(
            repo_path=publication_repo,
            github_repo="o/r",
            pr_number=99,
            merge_base_with="HEAD",
            config=load_config(env=_env()),
            inherited_final_tree_blockers=[blocker],
            skip_ops_gates=True,
        )

    assert job.pr_result.final_tree_blockers == [blocker]
    assert job.pr_result.publication_impact == "PUBLISH_RED"


def test_existing_ready_translation_pr_is_converted_to_draft(publication_repo: str):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        result,
        existing_pr=True,
        event_log=events,
    )

    assert getattr(job.pr_result, "publication_impact", None) == "PUBLISH_RED"
    gh.update_pull_body.assert_called_once()
    assert "QA RED, do not merge" in gh.update_pull_body.call_args.args[3]
    gh.convert_pull_to_draft.assert_called_once_with("o", "r", 99)
    assert events == ["discover", "draft", "push", "body"]
    gh.create_pull.assert_not_called()


def test_existing_ready_pr_conversion_failure_leaves_remote_untouched(
    publication_repo: str,
):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    with pytest.raises(GitHubAPIError, match="cannot convert"):
        _run_top_level(
            publication_repo,
            result,
            existing_pr=True,
            event_log=events,
            draft_conversion_fails=True,
        )

    assert events == ["discover", "draft"]


def test_late_existing_red_pr_is_converted_before_body_mutation(
    publication_repo: str,
):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    _job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        result,
        late_existing_pr=True,
        event_log=events,
    )

    assert events == ["discover", "create", "draft", "push", "body"]
    gh.convert_pull_to_draft.assert_called_once_with("o", "r", 99)


def test_late_existing_red_pr_conversion_failure_does_not_mutate_body(
    publication_repo: str,
):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    with pytest.raises(GitHubAPIError, match="cannot convert"):
        _run_top_level(
            publication_repo,
            result,
            late_existing_pr=True,
            event_log=events,
            draft_conversion_fails=True,
        )

    assert events == ["discover", "create", "draft"]


def test_post_push_existing_pr_fallback_converts_before_body_mutation(
    publication_repo: str,
):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    _run_top_level(
        publication_repo,
        result,
        late_existing_pr=True,
        remote_branch_exists=False,
        event_log=events,
    )

    assert events == ["discover", "push", "create", "draft", "body"]


def test_post_push_existing_pr_fallback_conversion_failure_is_fail_closed(
    publication_repo: str,
):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    with pytest.raises(GitHubAPIError, match="cannot convert"):
        _run_top_level(
            publication_repo,
            result,
            late_existing_pr=True,
            remote_branch_exists=False,
            event_log=events,
            draft_conversion_fails=True,
        )

    assert events == ["discover", "push", "create", "draft"]


def test_stale_remote_branch_without_diff_falls_through_to_post_push_draft_creation(
    publication_repo: str,
):
    result = _pair_result(target_text="See [missing](missing.md).\n")
    events: list[str] = []

    job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        result,
        remote_branch_exists=True,
        prepush_create_returns_none=True,
        event_log=events,
    )

    assert events == ["discover", "create", "push", "create"]
    assert job.translation_pr_number == 99
    assert [call.kwargs["draft"] for call in gh.create_pull.call_args_list] == [True, True]


def test_red_push_lease_failure_does_not_mutate_pull_request(
    publication_repo: str,
):
    events: list[str] = []

    with pytest.raises(RuntimeError, match="lease failed"):
        _run_top_level(
            publication_repo,
            _pair_result(target_text="See [missing](missing.md).\n"),
            remote_branch_exists=False,
            event_log=events,
            push_fails=True,
        )

    assert events == ["discover", "push"]


def test_failed_post_push_draft_conversion_deletes_new_red_remote_ref(
    publication_repo: str,
    tmp_path: Path,
):
    upstream = tmp_path / "rollback-upstream.git"
    subprocess.run(
        ["git", "clone", "--bare", publication_repo, str(upstream)],
        check=True,
        capture_output=True,
    )
    Path(publication_repo, "ydb/docs/en/a.md").write_text(
        "See [missing](missing.md).\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "ydb/docs/en/a.md"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "red candidate"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    red_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    events: list[str] = []
    rollback_result = _pair_result(target_text="See [missing](missing.md).\n")
    rollback_result.final_tree_blockers = [
        FinalTreeBlocker(
            path="ydb/docs/en/a.md",
            code="en_link_target",
            message="en_link_target: a.md: missing target",
        )
    ]

    with pytest.raises(GitHubAPIError, match="cannot convert"):
        _run_top_level(
            publication_repo,
            rollback_result,
            late_existing_pr=True,
            remote_branch_exists=False,
            event_log=events,
            draft_conversion_fails=True,
            real_push_remote=str(upstream),
        )

    remote_ref = subprocess.run(
        ["git", "--git-dir", str(upstream), "rev-parse", "refs/heads/ydbdoc-review/pr-7"],
        capture_output=True,
        text=True,
    )
    assert remote_ref.returncode != 0
    assert red_sha not in remote_ref.stdout
    assert events == ["discover", "push", "create", "draft"]


def test_failed_post_push_conversion_restores_remote_only_previous_sha(
    publication_repo: str,
    tmp_path: Path,
):
    upstream = tmp_path / "restore-upstream.git"
    subprocess.run(
        ["git", "clone", "--bare", publication_repo, str(upstream)],
        check=True,
        capture_output=True,
    )
    writer = tmp_path / "remote-writer"
    subprocess.run(
        ["git", "clone", str(upstream), str(writer)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "writer@example.com"],
        cwd=writer,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "remote writer"],
        cwd=writer,
        check=True,
    )
    Path(writer, "remote-only.txt").write_text("old remote tip\n", encoding="utf-8")
    subprocess.run(["git", "add", "remote-only.txt"], cwd=writer, check=True)
    subprocess.run(
        ["git", "commit", "-m", "remote-only old tip"],
        cwd=writer,
        check=True,
        capture_output=True,
    )
    previous_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=writer,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch_ref = "refs/heads/ydbdoc-review/pr-7"
    subprocess.run(
        ["git", "push", str(upstream), f"HEAD:{branch_ref}"],
        cwd=writer,
        check=True,
        capture_output=True,
    )
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", f"{previous_sha}^{{commit}}"],
            cwd=publication_repo,
            capture_output=True,
        ).returncode
        != 0
    )
    Path(publication_repo, "ydb/docs/en/a.md").write_text(
        "See [missing](missing.md).\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "ydb/docs/en/a.md"],
        cwd=publication_repo,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "red candidate"],
        cwd=publication_repo,
        check=True,
        capture_output=True,
    )
    rollback_result = _pair_result(target_text="See [missing](missing.md).\n")
    rollback_result.final_tree_blockers = [
        FinalTreeBlocker(
            path="ydb/docs/en/a.md",
            code="en_link_target",
            message="en_link_target: a.md: missing target",
        )
    ]

    with pytest.raises(GitHubAPIError, match="cannot convert"):
        _run_top_level(
            publication_repo,
            rollback_result,
            late_existing_pr=True,
            remote_branch_exists=True,
            remote_branch_sha=previous_sha,
            prepush_create_returns_none=True,
            draft_conversion_fails=True,
            real_push_remote=str(upstream),
        )

    restored_sha = subprocess.run(
        ["git", "--git-dir", str(upstream), "rev-parse", branch_ref],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert restored_sha == previous_sha


def _withhold_case(case: str) -> PRTranslationResult:
    if case == "pair_error":
        return _pair_result(target_text=None, error="translation failed")
    if case == "missing_expected_output":
        return _pair_result(target_text=None)
    if case == "translate_soft_keep":
        return _pair_result(
            target_text="Hello.\n",
            warnings=["translate_soft_keep: translate failed; kept tip EN unchanged"],
        )
    if case == "source_retaining_manual_action":
        return _pair_result(
            manual_actions=[ManualAction("s0001", "paragraph 1", "translate manually")]
        )
    if case == "segment_alignment":
        return _pair_result(alignment_error="expected 2 segments, got 1")
    if case == "deterministic_integrity":
        return _pair_result(
            link_issues=(
                LinkContractIssue("ambiguous_link_slot", "cannot prove source-owned slot"),
            )
        )
    if case == "include_parity":
        result = _pair_result(
            target_text="# Page\n\nTranslated body.\n",
            blocking=["include_parity: cannot auto-insert EN include"],
        )
        result.pair_results[0].source_text = (
            "# Страница\n\nТекст.\n\n"
            "{% include [note](./core/_includes/note.md) %}\n"
        )
        return result
    if case == "heading_parity":
        result = _pair_result(
            target_text="# Page\n\nTranslated body.\n",
            blocking=["heading_parity: source 2 headings vs target 1"],
        )
        result.pair_results[0].source_text = "# Страница\n\n## Раздел\n\nТекст.\n"
        return result
    if case == "list_tab_parity":
        result = _pair_result(
            target_text="# Page\n\nTranslated body.\n",
            blocking=["list_tab_parity: source 1 tab blocks vs target 0"],
        )
        result.pair_results[0].source_text = (
            "# Страница\n\n{% list tabs %}\n\n- Вкладка\n\n  Текст.\n\n"
            "{% endlist %}\n"
        )
        return result
    if case == "protect_marker_leakage":
        return _pair_result(
            target_text="Leaked ⟦C1⟧ marker.\n",
            blocking=["unrestored_placeholder: 1 leftover protect marker in EN"],
        )
    if case == "link_wrapper_loss":
        return _pair_result(
            link_issues=(
                LinkContractIssue("missing_link_wrapper", "source link wrapper was lost"),
            )
        )
    if case == "invalid_navigation_yaml":
        result = _pair_result()
        result.navigation_results = [
            NavigationRunResult(
                ru_path="ydb/docs/ru/core/toc_p.yaml",
                en_path="ydb/docs/en/core/toc_p.yaml",
                kind="toc",
                target_text="items: [",
                warnings=["invalid_yaml: expected closing bracket"],
                verdict="blocked",
            )
        ]
        return result
    raise AssertionError(f"unknown case: {case}")


@pytest.mark.parametrize(
    ("case", "expected_impact"),
    [
        ("pair_error", "WITHHOLD_INCOMPLETE"),
        ("missing_expected_output", "WITHHOLD_INCOMPLETE"),
        ("translate_soft_keep", "WITHHOLD_INCOMPLETE"),
        ("source_retaining_manual_action", "WITHHOLD_INCOMPLETE"),
        ("segment_alignment", "WITHHOLD_UNSAFE"),
        ("deterministic_integrity", "WITHHOLD_UNSAFE"),
        ("include_parity", "WITHHOLD_UNSAFE"),
        ("heading_parity", "WITHHOLD_UNSAFE"),
        ("list_tab_parity", "WITHHOLD_UNSAFE"),
        ("protect_marker_leakage", "WITHHOLD_UNSAFE"),
        ("link_wrapper_loss", "WITHHOLD_UNSAFE"),
        ("invalid_navigation_yaml", "WITHHOLD_UNSAFE"),
    ],
)
def test_withhold_never_prepares_or_publishes(
    publication_repo: str,
    case: str,
    expected_impact: str,
):
    result = _withhold_case(case)

    job, gh, prepare, commit, push, finish = _run_top_level(publication_repo, result)

    assert getattr(job.pr_result, "publication_impact", None) == expected_impact
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()
    assert finish.call_args.kwargs["status"] == "failed"
    assert job_requires_nonzero_exit(job) is True


def test_clean_candidate_keeps_normal_publication(publication_repo: str):
    result = _pair_result()

    job, gh, prepare, commit, push, finish = _run_top_level(publication_repo, result)

    assert getattr(job.pr_result, "publication_impact", None) == "PUBLISH_NORMAL"
    prepare.assert_called_once()
    commit.assert_called_once()
    push.assert_called_once()
    assert gh.create_pull.call_args.kwargs["draft"] is False
    assert "QA RED, do not merge" not in gh.create_pull.call_args.kwargs["body"]
    assert finish.call_args.kwargs["status"] == "ok"
    assert job_requires_nonzero_exit(job) is False


def test_clean_empty_scope_keeps_existing_success_without_pr(publication_repo: str):
    result = PRTranslationResult()

    job, gh, prepare, commit, push, finish = _run_top_level(
        publication_repo,
        result,
        source_changes=[],
    )

    assert job.pr_result.publication_impact == "PUBLISH_NORMAL"
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()
    assert finish.call_args.kwargs["status"] == "ok"
    assert job_requires_nonzero_exit(job) is False


def test_withhold_source_comment_names_file_reason_and_action(publication_repo: str):
    result = _withhold_case("include_parity")

    _job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        result,
    )

    source_summary = gh.post_issue_comment.call_args.args[3]
    assert "`ydb/docs/en/a.md`" in source_summary
    assert "include_parity:" in source_summary
    assert "Действие:" in source_summary
