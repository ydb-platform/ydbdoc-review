"""F-112 authorization and launch-boundary tests for doc_continue."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import run_doc_continue
from ydbdoc_review.ops.continue_cmd import find_latest_continue_instruction


def test_F112_latest_valid() -> None:
    comments = [
        {
            "body": "/ydbdoc continue use the authorized instruction",
            "created_at": "2026-09-12T10:00:00Z",
            "user": {"login": "reviewer", "type": "User"},
        },
        {
            "body": "/ydbdoc continue",
            "created_at": "2026-09-12T10:01:00Z",
            "user": {"login": "reviewer", "type": "User"},
        },
        {
            "body": "/ydbdoc continue use the stranger instruction",
            "created_at": "2026-09-12T10:02:00Z",
            "user": {"login": "stranger", "type": "User"},
        },
        {
            "body": "/ydbdoc continue use the bot instruction",
            "created_at": "2026-09-12T10:03:00Z",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        },
        {
            "body": "/ydbdoc continue use the late instruction",
            "created_at": "2026-09-12T10:05:00Z",
            "user": {"login": "reviewer", "type": "User"},
        },
    ]

    instruction = find_latest_continue_instruction(
        comments,
        allowed_actors=frozenset({"reviewer", "github-actions[bot]"}),
        before=datetime(2026, 9, 12, 10, 4, tzinfo=UTC),
    )

    assert instruction == "use the authorized instruction"


def test_F112_two_actors(monkeypatch) -> None:
    config = load_config(env={"GITHUB_TOKEN": "token"})
    context = SimpleNamespace(
        head_ref="feature",
        head_repo_full_name="o/r",
        labels=frozenset(),
    )
    gh = MagicMock()
    gh.iter_issue_comments.return_value = iter(
        [
            {
                "body": "/ydbdoc continue untrusted feedback",
                "created_at": "2026-09-12T10:00:00Z",
                "user": {"login": "stranger", "type": "User"},
            }
        ]
    )
    monkeypatch.setenv("YDBDOC_ALLOWED_ACTORS", "initiator,reviewer")
    monkeypatch.setenv("GITHUB_ACTOR", "initiator")

    with (
        patch("ydbdoc_review.github.workflow.load_config", return_value=config),
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh),
        patch("ydbdoc_review.github.workflow.pull_request_context", return_value=context),
        patch("ydbdoc_review.github.workflow.begin_ops_job") as begin_ops,
        patch("ydbdoc_review.github.workflow.run_doc_translate") as translate,
    ):
        result = run_doc_continue(
            repo_path=".",
            github_repo="o/r",
            pr_number=42,
            dry_run=True,
        )

    assert result.blocked is False
    begin_ops.assert_not_called()
    translate.assert_not_called()

    gh.iter_issue_comments.return_value = iter(
        [
            {
                "body": "/ydbdoc continue trusted feedback",
                "created_at": "2026-09-12T10:00:00Z",
                "user": {"login": "reviewer", "type": "User"},
            }
        ]
    )
    monkeypatch.setenv("GITHUB_ACTOR", "stranger")

    with (
        patch("ydbdoc_review.github.workflow.load_config", return_value=config),
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh),
        patch("ydbdoc_review.github.workflow.pull_request_context", return_value=context),
        patch("ydbdoc_review.github.workflow.run_doc_translate") as translate,
    ):
        result = run_doc_continue(
            repo_path=".",
            github_repo="o/r",
            pr_number=42,
            dry_run=True,
        )

    assert result.blocked is True
    translate.assert_not_called()
