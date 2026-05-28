"""GitHub branch delete status for translation publish."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from ydbdoc_review.github_api import delete_branch_if_exists


def test_delete_branch_returns_denied_on_403():
    resp = MagicMock(status_code=403)
    with patch("ydbdoc_review.github_api.httpx.delete", return_value=resp):
        assert (
            delete_branch_if_exists("o", "r", "ydbdoc-review/pr-1", "tok")
            == "denied"
        )


def test_delete_branch_returns_deleted_on_204():
    resp = MagicMock(status_code=204)
    with patch("ydbdoc_review.github_api.httpx.delete", return_value=resp):
        assert (
            delete_branch_if_exists("o", "r", "ydbdoc-review/pr-1", "tok")
            == "deleted"
        )


def test_delete_branch_raises_on_other_errors():
    resp = MagicMock(status_code=500)
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "err", request=MagicMock(), response=resp
    )
    with patch("ydbdoc_review.github_api.httpx.delete", return_value=resp):
        try:
            delete_branch_if_exists("o", "r", "b", "tok")
        except httpx.HTTPStatusError:
            pass
        else:
            raise AssertionError("expected HTTPStatusError")
