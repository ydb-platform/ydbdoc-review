"""F-092: an explicit translate event starts from a clean service artifact."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from ydbdoc_review.github import workflow


class _GitHub:
    def __init__(self, found: tuple[str, int] | None) -> None:
        self.found = found
        self.events: list[str] = []

    def find_open_pull_by_head(self, *_args, **_kwargs):
        self.events.append("find")
        return self.found

    def get_pull(self, *_args, **_kwargs):
        self.events.append("get")
        return {"head": {"ref": "ydbdoc-review/pr-42", "repo": {"full_name": "o/r"}}}

    def close_pull(self, *_args, **_kwargs):
        self.events.append("close")

    def get_branch_sha(self, *_args, **_kwargs):
        self.events.append("confirm")
        return "a" * 40

    def delete_branch(self, *_args, **_kwargs):
        self.events.append("delete")
        return True


def test_F092_restart_order() -> None:
    gh = _GitHub(("https://github.com/o/r/pull/99", 99))
    llm = Mock()

    workflow._restart_owned_translation_pr(
        gh,
        "o",
        "r",
        source_pr=42,
        branch="ydbdoc-review/pr-42",
        base="main",
        explicit=True,
    )
    llm.assert_not_called()
    assert gh.events == ["find", "get", "close", "confirm", "delete"]

    gh = _GitHub(("url", 99))
    gh.close_pull = Mock(side_effect=RuntimeError("close failed"))

    def cleanup_then_call_llm() -> None:
        workflow._restart_owned_translation_pr(
            gh,
            "o",
            "r",
            source_pr=42,
            branch="ydbdoc-review/pr-42",
            base="main",
            explicit=True,
        )
        llm()

    with pytest.raises(RuntimeError, match="close failed"):
        cleanup_then_call_llm()
    assert gh.events == ["find", "get"]
    llm.assert_not_called()


def test_F092_ownership_history() -> None:
    for explicit, found in ((True, None), (False, ("history", 7))):
        gh = _GitHub(found)
        workflow._restart_owned_translation_pr(
            gh,
            "o",
            "r",
            source_pr=42,
            branch="ydbdoc-review/pr-42",
            base="main",
            explicit=explicit,
        )
        assert gh.events == ["find"] if explicit else gh.events == []
