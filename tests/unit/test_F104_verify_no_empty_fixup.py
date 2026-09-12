"""F-104: read-only verify reports without empty fixup artifacts."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.pr import PullRequestContext
from ydbdoc_review.github.workflow import run_doc_verify


def _env() -> dict[str, str]:
    return {
        "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
        "YDBDOC_YC_FOLDER_ID": "b1",
        "YDBDOC_YC_API_KEY": "k",
        "GITHUB_TOKEN": "gh",
        "GITHUB_PUSH_TOKEN": "ghp",
    }


@pytest.fixture
def verify_repo(tmp_path: Path) -> tuple[str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "fixture"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    return str(repo), sha


def _run_read_only_verify(
    repo_path: str,
    sha: str,
    github: MagicMock,
    *,
    red: bool = False,
):
    ctx = PullRequestContext(
        owner="o",
        repo="r",
        number=104,
        title="Docs PR",
        head_ref="feature/docs",
        head_sha=sha,
        head_repo_full_name="o/r",
        head_repo_https_url="https://github.com/o/r.git",
        base_ref="main",
    )
    github.get_branch_sha.return_value = None

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=github),
        patch("ydbdoc_review.github.workflow.pull_request_context", return_value=ctx),
        patch("ydbdoc_review.github.workflow.list_pr_file_changes_git", return_value=[]),
        patch("ydbdoc_review.github.workflow.list_pr_file_changes_api", return_value=[]),
        patch(
            "ydbdoc_review.github.workflow.completeness_gaps",
            return_value=["ydb/docs/en/missing.md"] if red else [],
        ),
        patch("ydbdoc_review.github.workflow.prepare_translation_branch_on_base") as prepare,
        patch("ydbdoc_review.github.workflow.git_commit_paths") as commit,
        patch("ydbdoc_review.github.workflow.push_branch") as push,
    ):
        result = run_doc_verify(
            repo_path=repo_path,
            github_repo="o/r",
            pr_number=104,
            merge_base_with="HEAD",
            config=load_config(env=_env()),
            skip_ops_gates=True,
        )

    return result, prepare, commit, push


@pytest.mark.parametrize("red", [False, True], ids=["green", "red"])
def test_F104_no_changes(verify_repo: tuple[str, str], red: bool):
    repo_path, sha = verify_repo
    github = MagicMock()
    github.iter_issue_comments.return_value = iter([])
    github.post_issue_comment.return_value = "report-url"

    result, prepare, commit, push = _run_read_only_verify(
        repo_path, sha, github, red=red
    )

    prepare.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    github.create_pull.assert_not_called()
    assert result.committed is False
    assert result.pushed is False
    github.post_issue_comment.assert_called_once()
    assert github.post_issue_comment.call_args.args[2] == 104
    if red:
        assert "🔴" in github.post_issue_comment.call_args.args[3]


def test_F104_report_history(verify_repo: tuple[str, str]):
    repo_path, sha = verify_repo
    github = MagicMock()
    legacy = {"body": "🤖 ydbdoc-review — отчёт #1 (doc_verify, legacy UTC)"}
    comments: list[dict[str, str]] = [legacy]

    github.iter_issue_comments.side_effect = lambda *_args: iter(comments)

    def _post(*args):
        comments.append({"body": args[3]})
        return f"report-{len(comments)}"

    github.post_issue_comment.side_effect = _post

    _run_read_only_verify(repo_path, sha, github)
    _run_read_only_verify(repo_path, sha, github)

    assert len(comments) == 3
    assert comments[0] is legacy
    first, second = (comment["body"] for comment in comments[1:])
    assert "отчёт №2" in first
    assert "отчёт №3" in second
    assert "UTC" in first and f"Checkout: `{sha[:12]}`" in first
    assert "UTC" in second and f"Checkout: `{sha[:12]}`" in second
