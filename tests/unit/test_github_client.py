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
    assert opened == ("https://github.com/o/r/pull/9", 9, True)


@patch("ydbdoc_review.github.client.requests.request")
def test_create_pull_can_open_draft(mock_request):
    mock_request.side_effect = [
        MagicMock(status_code=200, content=b"[]", json=lambda: []),
        MagicMock(
            status_code=201,
            content=b'{"html_url": "https://github.com/o/r/pull/9", "number": 9}',
            json=lambda: {"html_url": "https://github.com/o/r/pull/9", "number": 9},
        ),
    ]
    client = GitHubClient("tok")

    opened = client.create_pull(
        "o", "r", title="t", head="h", base="b", body="body", draft=True
    )

    assert opened == ("https://github.com/o/r/pull/9", 9, True)
    assert mock_request.call_args.kwargs["json"]["draft"] is True


def test_get_branch_sha_returns_exact_remote_object_sha():
    client = GitHubClient("tok")
    with patch.object(
        client,
        "_request",
        return_value={"object": {"type": "commit", "sha": "abc123"}},
    ):
        assert client.get_branch_sha("o", "r", "a/b") == "abc123"
        client._request.assert_called_once_with(
            "GET",
            "https://api.github.com/repos/o/r/git/ref/heads/a%2Fb",
        )


def test_get_branch_sha_returns_none_for_absent_ref():
    client = GitHubClient("tok")
    with patch.object(
        client,
        "_request",
        side_effect=GitHubAPIError("missing", status_code=404),
    ):
        assert client.get_branch_sha("o", "r", "missing") is None


@patch("ydbdoc_review.github.client.requests.request")
def test_convert_ready_pull_to_draft(mock_request):
    mock_request.side_effect = [
        MagicMock(
            status_code=200,
            content=b'{"draft": false, "node_id": "PR_node"}',
            json=lambda: {"draft": False, "node_id": "PR_node"},
        ),
        MagicMock(
            status_code=200,
            content=b"{}",
            json=lambda: {
                "data": {
                    "convertPullRequestToDraft": {"pullRequest": {"isDraft": True}}
                }
            },
        ),
    ]
    client = GitHubClient("tok")

    assert client.convert_pull_to_draft("o", "r", 9) is True
    method, url = mock_request.call_args.args[:2]
    assert method == "POST"
    assert url == "https://api.github.com/graphql"
    assert mock_request.call_args.kwargs["json"]["variables"] == {"id": "PR_node"}


@patch("ydbdoc_review.github.client.requests.request")
def test_convert_ready_pull_to_draft_rejects_unconfirmed_conversion(mock_request):
    mock_request.side_effect = [
        MagicMock(
            status_code=200,
            content=b'{"draft": false, "node_id": "PR_node"}',
            json=lambda: {"draft": False, "node_id": "PR_node"},
        ),
        MagicMock(
            status_code=200,
            content=b'{"errors": [{"message": "forbidden"}]}',
            json=lambda: {"errors": [{"message": "forbidden"}]},
        ),
    ]
    client = GitHubClient("tok")

    with pytest.raises(GitHubAPIError, match="did not convert"):
        client.convert_pull_to_draft("o", "r", 9)








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
