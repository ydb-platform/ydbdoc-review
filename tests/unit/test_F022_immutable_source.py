"""F-022: source content is read from one immutable snapshot per run."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ydbdoc_review.github.pr import (
    source_pr_content_ref,
    source_pr_content_ref_from_pull,
)


def _pull(*, merged: bool, head_sha: str = "author-head", merge_sha: str = "") -> dict:
    return {
        "merged": merged,
        "merge_commit_sha": merge_sha,
        "head": {
            "sha": head_sha,
            "repo": {"owner": {"login": "contributor"}, "name": "ydb"},
        },
        "base": {"ref": "main", "sha": "later-base"},
    }


@pytest.mark.parametrize(
    ("mode", "pull", "expected"),
    [
        ("open", _pull(merged=False), ("contributor", "ydb", "author-head")),
        ("merge", _pull(merged=True, merge_sha="merge-result"), ("ydb-platform", "ydb", "merge-result")),
        ("squash", _pull(merged=True, merge_sha="squash-result"), ("ydb-platform", "ydb", "squash-result")),
        ("rebase", _pull(merged=True, merge_sha="rebased-result"), ("ydb-platform", "ydb", "rebased-result")),
    ],
)
def test_F022_source_modes(mode: str, pull: dict, expected: tuple[str, str, str]) -> None:
    del mode
    assert source_pr_content_ref_from_pull(pull, "ydb-platform", "ydb", 22) == expected
    assert expected[2] != pull["base"]["sha"]


def test_F022_deleted_branch_uses_captured_merge_snapshot_without_reread() -> None:
    gh = MagicMock()
    gh.get_pull.return_value = _pull(merged=True, head_sha="deleted-branch", merge_sha="landed")

    snapshot = source_pr_content_ref(gh, "ydb-platform", "ydb", 22)
    gh.get_pull.return_value["head"]["sha"] = "changed-after-capture"

    assert snapshot == ("ydb-platform", "ydb", "landed")
    gh.get_pull.assert_called_once_with("ydb-platform", "ydb", 22)

