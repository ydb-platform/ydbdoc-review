"""F-005: translation work remains executable without publication."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ydbdoc_review.github import workflow
from ydbdoc_review.github.workflow import DocJobResult
from ydbdoc_review.pipeline.types import PRTranslationResult


@pytest.mark.parametrize("flag", ["dry_run", "no_commit"])
def test_F005_nonpublishing_modes_finalize_ops_and_report_no_pr(monkeypatch, flag):
    usage = SimpleNamespace(
        records=[SimpleNamespace(input_tokens=3, output_tokens=2, success=True)],
        estimate_cost_rub=Mock(return_value=1.25),
        total_input_tokens=3,
        total_output_tokens=2,
    )
    client = SimpleNamespace(usage_tracker=usage)
    ops_ctx = SimpleNamespace()
    job = DocJobResult(
        mode="doc_translate",
        pr_number=7,
        pr_result=PRTranslationResult(),
        dry_run=flag == "dry_run",
    )
    finished = Mock()

    monkeypatch.setattr(workflow, "finish_ops_job", finished)

    if flag == "dry_run":
        workflow._finish_nonpublishing_translate_job(job, client, ops_ctx)
    else:
        workflow._finish_nonpublishing_translate_job(job, client, ops_ctx)

    finished.assert_called_once()
    assert finished.call_args.kwargs["cost_rub"] == 1.25
    assert job.translation_pr_number is None
    assert job.committed is False
    assert job.pushed is False


def test_F005_noop_suppresses_comment_for_no_commit(monkeypatch):
    comment = Mock()
    monkeypatch.setattr(workflow, "_safe_post_issue_comment", comment)
    assert workflow._publication_side_effects_allowed(dry_run=False, no_commit=True) is False
    assert workflow._publication_side_effects_allowed(dry_run=True, no_commit=False) is False
    assert workflow._publication_side_effects_allowed(dry_run=False, no_commit=False) is True
    comment.assert_not_called()


def test_F005_continue_no_commit_suppresses_ops_deny_comment(monkeypatch):
    cfg = SimpleNamespace(
        paths=SimpleNamespace(
            translation_branch_prefix="ydbdoc-review/pr-",
            verify_fixup_branch_prefix="ydbdoc-review/verify-",
        )
    )
    monkeypatch.setattr(workflow, "load_config", lambda: cfg)
    monkeypatch.setattr(workflow, "_github_tokens", lambda _cfg: ("api", "push"))
    monkeypatch.setattr(workflow, "parse_repo", lambda _repo: ("owner", "repo"))
    monkeypatch.setattr(workflow, "GitHubClient", lambda _token: object())
    monkeypatch.setattr(
        workflow,
        "pull_request_context",
        lambda *_args: SimpleNamespace(head_ref="ydbdoc-review/pr-7"),
    )
    monkeypatch.setattr(
        workflow,
        "begin_ops_job",
        lambda **_kwargs: (SimpleNamespace(), SimpleNamespace(ok=False), "deny"),
    )
    comment = Mock()
    monkeypatch.setattr(workflow, "_safe_post_issue_comment", comment)

    result = workflow.run_doc_continue(
        repo_path="/tmp/repo",
        github_repo="owner/repo",
        pr_number=7,
        instruction="feedback",
        no_commit=True,
    )

    assert result.blocked is True
    comment.assert_not_called()
