"""F-100: final reports describe only a confirmed published candidate K."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.test_publication_policy import (
    _pair_result,
    _run_top_level,
)
from tests.unit.test_publication_policy import (
    publication_repo as _publication_repo_fixture,
)
from ydbdoc_review.github.pr import PullRequestContext
from ydbdoc_review.github.workflow import _await_inline_fixup_pr_context

publication_repo = _publication_repo_fixture


def _context(sha: str) -> PullRequestContext:
    return PullRequestContext(
        owner="o",
        repo="r",
        number=100,
        title="Translation",
        head_ref="ydbdoc-review/pr-100",
        head_sha=sha,
        head_repo_full_name="o/r",
        head_repo_https_url="https://github.com/o/r.git",
        base_ref="main",
        base_sha="a" * 40,
        body="confirmed authority",
    )


def test_F100_latest_k() -> None:
    """One immutable snapshot confirms branch, PR, and latest post-fix K."""
    previous = _context("b" * 40)
    latest = replace(previous, head_sha="c" * 40)
    gh = MagicMock()
    gh.get_branch_sha.return_value = latest.head_sha

    with (
        patch(
            "ydbdoc_review.github.workflow.resolve_commit_ref",
            return_value=latest.head_sha,
        ) as resolve,
        patch(
            "ydbdoc_review.github.workflow.pull_request_context",
            return_value=latest,
        ) as read_pr,
    ):
        confirmed = _await_inline_fixup_pr_context(
            gh,
            "o",
            "r",
            100,
            repo_path="/repo",
            previous_context=previous,
            expected_sha=latest.head_sha,
        )

    assert confirmed.head_sha == latest.head_sha
    gh.get_branch_sha.assert_called_once_with(
        "o", "r", "ydbdoc-review/pr-100"
    )
    read_pr.assert_called_once_with(gh, "o", "r", 100)
    resolve.assert_called_once_with("/repo", "HEAD")


def test_F100_push_failure(publication_repo: str) -> None:
    """Push or confirmation failure requires a full manual retry without polling."""
    with pytest.raises(RuntimeError) as raised:
        _run_top_level(
            publication_repo,
            _pair_result(target_text="Latest correction.\n"),
            push_fails=True,
        )

    message = str(raised.value)
    assert "publication failed" in message.casefold()
    assert "ydbdoc-review/pr-7" in message
    assert "K=" in message
    assert "`doc_translate`" in message
    assert "manual" in message.casefold()
    assert "automatic retry" in message.casefold()

    previous = _context("b" * 40)
    expected_k = "c" * 40
    stale = replace(previous, head_sha=previous.head_sha)
    gh = MagicMock()
    gh.get_branch_sha.return_value = expected_k
    with (
        patch(
            "ydbdoc_review.github.workflow.resolve_commit_ref",
            return_value=expected_k,
        ),
        patch(
            "ydbdoc_review.github.workflow.pull_request_context",
            return_value=stale,
        ) as read_pr,
        patch("ydbdoc_review.github.workflow.time.sleep") as sleep,
        pytest.raises(RuntimeError) as unconfirmed,
    ):
        _await_inline_fixup_pr_context(
            gh,
            "o",
            "r",
            100,
            repo_path="/repo",
            previous_context=previous,
            expected_sha=expected_k,
        )

    confirmation_message = str(unconfirmed.value)
    assert "publication failed" in confirmation_message.casefold()
    assert "PR=#100" in confirmation_message
    assert f"K={expected_k}" in confirmation_message
    assert "`doc_translate`" in confirmation_message
    read_pr.assert_called_once()
    sleep.assert_not_called()
