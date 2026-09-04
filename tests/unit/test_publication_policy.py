"""Top-level publication contract for paid doc_translate candidates."""

from __future__ import annotations

import subprocess
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import (
    job_requires_nonzero_exit,
    run_doc_translate,
)
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
)
from ydbdoc_review.reporting.builder import ReportMeta, build_full_report
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
    impact_path: str | None = None,
    create_succeeds: bool = True,
    source_changes: list[tuple[str, str]] | None = None,
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
    gh.create_pull.return_value = (
        ("https://github.com/o/r/pull/99", 99, not existing_pr)
        if create_succeeds
        else None
    )
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
        push = stack.enter_context(patch("ydbdoc_review.github.workflow.push_branch"))
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


def test_existing_ready_translation_pr_is_converted_to_draft(publication_repo: str):
    result = _pair_result(target_text="See [missing](missing.md).\n")

    job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        result,
        existing_pr=True,
    )

    assert getattr(job.pr_result, "publication_impact", None) == "PUBLISH_RED"
    gh.update_pull_body.assert_called_once()
    assert "QA RED, do not merge" in gh.update_pull_body.call_args.args[3]
    gh.convert_pull_to_draft.assert_called_once_with("o", "r", 99)


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
