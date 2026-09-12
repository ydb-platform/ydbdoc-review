"""F-098: unresolved instructions never become a green no-op artifact."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_publication_policy import (
    _pair_result,
    _run_top_level,
)
from tests.unit.test_publication_policy import (
    publication_repo as _publication_repo_fixture,
)
from ydbdoc_review.github.provenance import parse_authority_evidence
from ydbdoc_review.ops.job_state import load_continuability
from ydbdoc_review.pipeline.types import NavigationRunResult, PRTranslationResult

publication_repo = _publication_repo_fixture


def _unresolved_result() -> PRTranslationResult:
    return PRTranslationResult(
        navigation_results=[
            NavigationRunResult(
                ru_path="ydb/docs/ru/core/guide/toc.yaml",
                en_path="ydb/docs/en/core/guide/toc.yaml",
                kind="toc",
                warnings=[
                    "orphan_toc_page: old EN route has no unambiguous successor"
                ],
                verdict="blocked",
            )
        ],
        publication_failure="awaiting_instruction_no_artifact",
    )


def test_F098_delete_only(publication_repo: str) -> None:
    old_file = Path(publication_repo, "ydb/docs/en/a.md")

    job, gh, prepare, commit, push, finish = _run_top_level(
        publication_repo,
        _unresolved_result(),
        source_changes=[("ydb/docs/ru/a.md", "deleted")],
    )

    assert old_file.read_text(encoding="utf-8") == "Hello.\n"
    assert job.pr_result.publication_failure == "awaiting_instruction_no_artifact"
    assert job.translation_pr_number is None
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()
    source_comment = gh.post_issue_comment.call_args.args[3]
    assert "какой существующий адрес" in source_comment
    assert "/ydbdoc continue" in source_comment
    assert finish.call_args.kwargs["status"] == "failed"
    state = load_continuability(publication_repo, 7)
    assert state is not None
    assert state.translation_pr is None
    assert state.fixed_shas
    assert state.allows_continue()


def test_F098_continue_existing(publication_repo: str) -> None:
    old_file = Path(publication_repo, "ydb/docs/en/a.md")

    job, gh, prepare, commit, push, finish = _run_top_level(
        publication_repo,
        _unresolved_result(),
        existing_pr=True,
        remote_branch_exists=True,
        continue_pr_number=99,
        real_ops_evidence=True,
    )

    assert old_file.read_text(encoding="utf-8") == "Hello.\n"
    assert job.translation_pr_number == 99
    assert job.pr_result.publication_failure == "awaiting_instruction_no_artifact"
    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    gh.create_pull.assert_not_called()
    red_comments = [
        call.args[3]
        for call in gh.post_issue_comment.call_args_list
        if call.args[2] == 99
    ]
    assert len(red_comments) == 1
    assert "QA RED" in red_comments[0]
    assert "QA RED" in gh.update_pull_body.call_args.args[3]
    preserved = parse_authority_evidence(gh.update_pull_body.call_args.args[3])
    assert preserved.coverage_version == 1
    assert preserved.coverage_run_id == "parent-run"
    assert preserved.coverage_digest == "a" * 64
    source_comments = [
        call.args[3]
        for call in gh.post_issue_comment.call_args_list
        if call.args[2] == 7
    ]
    assert len(source_comments) == 1
    assert "Translation PR | #99" in source_comments[0]
    assert "/ydbdoc continue" in source_comments[0]
    state = load_continuability(publication_repo, 7)
    assert state is not None
    assert state.translation_pr == 99
    assert state.fixed_shas
    assert state.allows_continue()
    assert finish.call_args.kwargs["status"] == "published_red"
    ops_ctx = finish.call_args.args[0]
    assert ops_ctx.ledger.records[0].run_id == "continue-run"
    assert ops_ctx.ledger.records[0].status == "published_red"
    assert ops_ctx.store.get("continue-run", "manifest.json") is not None


def test_F098_continue_decision_with_material_diff_publishes_artifact(
    publication_repo: str,
) -> None:
    _run_top_level(
        publication_repo,
        _unresolved_result(),
        source_changes=[("ydb/docs/ru/a.md", "deleted")],
    )
    job, _gh, prepare, commit, push, _finish = _run_top_level(
        publication_repo,
        _pair_result(target_text="Resolved translation.\n"),
        source_changes=[("ydb/docs/ru/a.md", "deleted")],
        continue_source_pr=True,
        real_git_commit=True,
    )

    assert job.translation_pr_number == 99
    assert job.committed is True
    assert job.pushed is True
    prepare.assert_called_once()
    commit.assert_called_once()
    push.assert_called_once()
    assert Path(publication_repo, "ydb/docs/en/a.md").read_text(encoding="utf-8") == (
        "Resolved translation.\n"
    )


def test_F098_existing_pr_metadata_failure_keeps_continue_evidence(
    publication_repo: str,
) -> None:
    with pytest.raises(RuntimeError, match="metadata unavailable"):
        _run_top_level(
            publication_repo,
            _unresolved_result(),
            existing_pr=True,
            remote_branch_exists=True,
            continue_pr_number=99,
            real_ops_evidence=True,
            update_body_error=RuntimeError("metadata unavailable"),
        )

    state = load_continuability(publication_repo, 7)
    assert state is not None
    assert state.translation_pr == 99
    assert state.fixed_shas
    assert state.allows_continue()
