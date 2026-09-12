from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import DocJobResult, run_doc_continue
from ydbdoc_review.ops.lifecycle import GateResult


@pytest.fixture
def git_repo(tmp_path: Path) -> str:
    return str(tmp_path)


def _env() -> dict[str, str]:
    return {
        "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
        "YDBDOC_YC_FOLDER_ID": "b1",
        "YDBDOC_YC_API_KEY": "k",
        "GITHUB_TOKEN": "gh",
        "GITHUB_PUSH_TOKEN": "ghp",
        "YDBDOC_SKIP_OPS_GATES": "1",
    }


def _make_pull(head_ref: str, title: str = "Auto-translate docs from PR #40385") -> dict[str, object]:
    return {
        "title": title,
        "head": {
            "ref": head_ref,
            "sha": "abc",
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "user": {"login": "github-actions[bot]"},
        "base": {"ref": "main"},
    }


@dataclass(frozen=True)
class _FakeOpsContext:
    source_pr: int


@pytest.mark.parametrize(
    "feedback",
    (
        "QA: unresolved links in translated anchors",
        "Update TOC for renamed section",
        "Fix redirects for orphaned toc pages",
    ),
)
def test_F114_red_tasks(git_repo: str, feedback: str) -> None:
    pull = _make_pull("ydbdoc-review/pr-40385")
    translated = DocJobResult(mode="doc_continue", pr_number=40385)
    with (
        patch("ydbdoc_review.github.workflow.begin_ops_job") as mock_begin,
        patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh,
        patch(
            "ydbdoc_review.github.workflow.run_doc_translate",
            return_value=translated,
        ) as translate,
        patch("ydbdoc_review.github.workflow.run_doc_verify") as verify,
    ):
        mock_begin.return_value = (
            _FakeOpsContext(source_pr=40385),
            GateResult(ok=True),
            None,
        )
        mock_gh.return_value.get_pull.return_value = pull
        result = run_doc_continue(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=50840,
            merge_base_with="HEAD",
            dry_run=True,
            config=load_config(env=_env()),
            instruction=feedback,
        )

    assert result is translated
    translate.assert_called_once()
    assert translate.call_args.kwargs["continue_feedback"] == feedback
    verify.assert_not_called()


def test_F114_completed_run(git_repo: str) -> None:
    pull = _make_pull("ydbdoc-review/pr-40385")
    translated = DocJobResult(mode="doc_continue", pr_number=40385)

    with (
        patch("ydbdoc_review.github.workflow.begin_ops_job") as mock_begin,
        patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh,
    ):
        mock_begin.return_value = (
            _FakeOpsContext(source_pr=40385),
            GateResult(ok=True),
            None,
        )
        mock_gh.return_value.get_pull.return_value = pull
        with patch(
            "ydbdoc_review.github.workflow.run_doc_translate",
            return_value=translated,
        ) as translate:
            with patch("ydbdoc_review.github.workflow.run_doc_verify") as verify:
                result = run_doc_continue(
                    repo_path=git_repo,
                    github_repo="o/r",
                    pr_number=50840,
                    merge_base_with="HEAD",
                    dry_run=True,
                    config=load_config(env=_env()),
                    instruction="Fix links and TOC consistency after red QA",
                )

    assert result is translated
    assert result.blocked is False
    translate.assert_called_once()
    verify.assert_not_called()
