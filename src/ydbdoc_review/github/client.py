"""GitHub REST API client (uses ``requests``)."""

from __future__ import annotations

import base64
from typing import Any, Iterator
from urllib.parse import quote

import requests

from ydbdoc_review.github.errors import GitHubAPIError
from ydbdoc_review.llm.tls import public_ca_bundle

_API_VERSION = "2022-11-28"
_DEFAULT_TIMEOUT = 120.0


class GitHubClient:
    """Minimal GitHub REST client for PR workflow."""

    def __init__(self, token: str, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        if not token:
            raise GitHubAPIError("GitHub token is required")
        self._token = token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        resp = requests.request(
            method,
            url,
            headers=self._headers(),
            params=params,
            json=json_body,
            timeout=self._timeout,
            verify=public_ca_bundle(),
        )
        if resp.status_code >= 400:
            raise GitHubAPIError(
                f"GitHub API {method} {url} failed: HTTP {resp.status_code} {resp.text[:300]}",
                status_code=resp.status_code,
            )
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def get_pull(self, owner: str, repo: str, pr_number: int) -> dict[str, Any]:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
        data = self._request("GET", url)
        assert isinstance(data, dict)
        return data

    def iter_pull_files(
        self, owner: str, repo: str, pr_number: int
    ) -> Iterator[dict[str, Any]]:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
        page = 1
        while True:
            batch = self._request(
                "GET", url, params={"per_page": 100, "page": page}
            )
            if not isinstance(batch, list) or not batch:
                return
            yield from batch
            if len(batch) < 100:
                return
            page += 1

    def get_file_text(
        self, owner: str, repo: str, path: str, ref: str
    ) -> str | None:
        enc_path = quote(path, safe="/")
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{enc_path}"
        try:
            data = self._request("GET", url, params={"ref": ref})
        except GitHubAPIError as exc:
            if exc.status_code == 404:
                return None
            raise
        if not isinstance(data, dict):
            return None
        if data.get("encoding") != "base64" or "content" not in data:
            return None
        raw = base64.b64decode(str(data["content"]).replace("\n", ""))
        return raw.decode("utf-8")

    def iter_issue_comments(
        self, owner: str, repo: str, issue_number: int
    ) -> Iterator[dict[str, Any]]:
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/issues/"
            f"{issue_number}/comments"
        )
        page = 1
        while True:
            batch = self._request(
                "GET", url, params={"per_page": 100, "page": page}
            )
            if not isinstance(batch, list) or not batch:
                return
            yield from batch
            if len(batch) < 100:
                return
            page += 1

    def post_issue_comment(
        self, owner: str, repo: str, pr_number: int, body: str
    ) -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
        data = self._request("POST", url, json_body={"body": body})
        assert isinstance(data, dict)
        return str(data.get("html_url", ""))

    def find_open_pull_by_head(
        self, owner: str, repo: str, *, head_branch: str, base: str
    ) -> tuple[str, int] | None:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        pulls = self._request(
            "GET",
            url,
            params={"state": "open", "head": f"{owner}:{head_branch}", "base": base},
        )
        if not isinstance(pulls, list) or not pulls:
            return None
        item = pulls[0]
        html = str(item.get("html_url", ""))
        num = int(item.get("number", 0))
        return (html, num) if html and num else None

    def delete_branch(self, owner: str, repo: str, branch: str) -> bool:
        """Delete ``refs/heads/{branch}``. Return True if removed, False if absent."""
        enc_branch = quote(branch, safe="")
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{enc_branch}"
        )
        try:
            self._request("DELETE", url)
        except GitHubAPIError as exc:
            if exc.status_code in (404, 422):
                return False
            raise
        return True

    def add_issue_labels(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        labels: list[str],
    ) -> None:
        """Add labels to a PR/issue (PRs are issues in the GitHub API)."""
        if not labels:
            return
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/issues/"
            f"{issue_number}/labels"
        )
        self._request("POST", url, json_body={"labels": labels})

    def create_pull(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str,
    ) -> tuple[str, int, bool] | None:
        """Open a PR or return an existing one.

        Returns ``(html_url, number, created)`` where ``created`` is False if the
        PR already existed for the same head/base.
        """
        existing = self.find_open_pull_by_head(
            owner, repo, head_branch=head, base=base
        )
        if existing:
            url, num = existing
            return url, num, False
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        try:
            data = self._request(
                "POST",
                url,
                json_body={"title": title, "head": head, "base": base, "body": body},
            )
        except GitHubAPIError as exc:
            if exc.status_code == 422:
                found = self.find_open_pull_by_head(
                    owner, repo, head_branch=head, base=base
                )
                if found:
                    u, n = found
                    return u, n, False
                return None
            raise
        if not isinstance(data, dict):
            return None
        html = str(data.get("html_url", ""))
        num = int(data.get("number", 0))
        return (html, num, True) if html and num else None
