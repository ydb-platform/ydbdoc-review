"""F-146: explicit restart may replace only an owned service artifact."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from ydbdoc_review.github import workflow


class _GitHub:
    def __init__(self, *, user: str = "github-actions[bot]", branch_sha: str | None = "a" * 40):
        self.user = user
        self.branch_sha = branch_sha
        self.events: list[str] = []

    def find_open_pull_by_head(self, *_args, **_kwargs):
        self.events.append("find")
        return ("https://github.com/o/r/pull/99", 99)

    def get_pull(self, *_args, **_kwargs):
        self.events.append("get")
        return {
            "user": {"login": self.user},
            "head": {"ref": "ydbdoc-review/pr-42", "repo": {"full_name": "o/r"}},
            "body": "old service result",
        }

    def close_pull(self, *_args, **_kwargs):
        self.events.append("close")

    def get_branch_sha(self, *_args, **_kwargs):
        self.events.append("confirm")
        return self.branch_sha

    def delete_branch(self, *_args, **_kwargs):
        self.events.append("delete")
        return True


def test_F146_ownership() -> None:
    owned = _GitHub()
    workflow._restart_owned_translation_pr(
        owned, "o", "r", source_pr=42, branch="ydbdoc-review/pr-42", base="main", explicit=True
    )
    assert owned.events == ["find", "get", "close", "confirm", "delete"]

    human = _GitHub(user="alice")
    with pytest.raises(RuntimeError, match="non-service"):
        workflow._restart_owned_translation_pr(
            human, "o", "r", source_pr=42, branch="ydbdoc-review/pr-42", base="main", explicit=True
        )
    assert human.events == ["find", "get"]


def test_F146_denial_missing() -> None:
    absent = _GitHub(branch_sha=None)
    workflow._restart_owned_translation_pr(
        absent, "o", "r", source_pr=42, branch="ydbdoc-review/pr-42", base="main", explicit=True
    )
    assert absent.events == ["find", "get", "close", "confirm"]

    denied = _GitHub()
    workflow._restart_owned_translation_pr(
        denied, "o", "r", source_pr=42, branch="ydbdoc-review/pr-42", base="main", explicit=False
    )
    assert denied.events == []

    failure = _GitHub()
    failure.close_pull = Mock(side_effect=RuntimeError("close failed"))
    try:
        workflow._restart_owned_translation_pr(
            failure,
            "o",
            "r",
            source_pr=42,
            branch="ydbdoc-review/pr-42",
            base="main",
            explicit=True,
        )
    except RuntimeError as exc:
        assert str(exc) == "close failed"
    else:
        raise AssertionError("close failure must stop restart before branch deletion")
    assert failure.events == ["find", "get"]
