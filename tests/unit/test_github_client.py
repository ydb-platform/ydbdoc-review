"""Tests for GitHub REST client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.github.errors import GitHubAPIError


def test_client_requires_token():
    with pytest.raises(GitHubAPIError, match="token"):
        GitHubClient("")


@patch("ydbdoc_review.github.client.requests.request")
def test_get_pull(mock_request):
    mock_request.return_value = MagicMock(
        status_code=200,
        content=b'{"number": 1, "title": "t"}',
        json=lambda: {"number": 1, "title": "t"},
    )
    client = GitHubClient("tok")
    data = client.get_pull("o", "r", 1)
    assert data["number"] == 1
    mock_request.assert_called_once()


@patch("ydbdoc_review.github.client.requests.request")
def test_get_file_text_404(mock_request):
    mock_request.return_value = MagicMock(
        status_code=404,
        text="not found",
        content=b"",
    )
    client = GitHubClient("tok")
    assert client.get_file_text("o", "r", "a.md", "main") is None


@patch("ydbdoc_review.github.client.requests.request")
def test_iter_pull_files_pagination(mock_request):
    page1 = [{"filename": "a.md", "status": "modified"}]
    page2: list[dict] = []
    mock_request.side_effect = [
        MagicMock(status_code=200, content=b"[]", json=lambda: page1),
        MagicMock(status_code=200, content=b"[]", json=lambda: page2),
    ]
    client = GitHubClient("tok")
    files = list(client.iter_pull_files("o", "r", 5))
    assert len(files) == 1


@patch("ydbdoc_review.github.client.requests.request")
def test_create_pull_idempotent(mock_request):
    mock_request.return_value = MagicMock(
        status_code=201,
        content=b'{"html_url": "https://github.com/o/r/pull/9", "number": 9}',
        json=lambda: {"html_url": "https://github.com/o/r/pull/9", "number": 9},
    )
    client = GitHubClient("tok")
    opened = client.create_pull(
        "o", "r", title="t", head="h", base="b", body="body"
    )
    assert opened == ("https://github.com/o/r/pull/9", 9)


@patch("ydbdoc_review.github.client.requests.request")
def test_get_file_text_success(mock_request):
    import base64

    payload = base64.b64encode(b"hello").decode()
    mock_request.return_value = MagicMock(
        status_code=200,
        content=b"{}",
        json=lambda: {"encoding": "base64", "content": payload + "\n"},
    )
    client = GitHubClient("tok")
    assert client.get_file_text("o", "r", "a.md", "main") == "hello"


@patch("ydbdoc_review.github.client.requests.request")
def test_post_issue_comment(mock_request):
    mock_request.return_value = MagicMock(
        status_code=201,
        content=b'{"html_url": "https://github.com/o/r/issues/1#issuecomment-1"}',
        json=lambda: {"html_url": "https://github.com/o/r/issues/1#issuecomment-1"},
    )
    client = GitHubClient("tok")
    url = client.post_issue_comment("o", "r", 1, "hi")
    assert "comment" in url


@patch("ydbdoc_review.github.client.requests.request")
def test_request_raises_api_error(mock_request):
    mock_request.return_value = MagicMock(status_code=500, text="boom", content=b"")
    client = GitHubClient("tok")
    with pytest.raises(GitHubAPIError, match="500"):
        client.get_pull("o", "r", 1)


@patch("ydbdoc_review.github.client.requests.request")
def test_iter_issue_comments(mock_request):
    mock_request.return_value = MagicMock(
        status_code=200,
        content=b"[]",
        json=lambda: [{"body": "ydbdoc-review — отчёт #1"}],
    )
    client = GitHubClient("tok")
    comments = list(client.iter_issue_comments("o", "r", 2))
    assert len(comments) == 1

