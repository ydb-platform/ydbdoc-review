"""GitHub REST API client (uses ``requests``)."""

from __future__ import annotations

import base64
import re
import uuid
from collections.abc import Iterator
from typing import Any
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
            if not isinstance(batch, list):
                raise GitHubAPIError("Malformed PR files response")
            if not batch:
                return
            yield from batch
            if len(batch) < 100:
                return
            page += 1


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
        self._check_report_body(body)
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
        data = self._request("POST", url, json_body={"body": body})
        assert isinstance(data, dict)
        return str(data.get("html_url", ""))

    @staticmethod
    def _check_report_body(body: str) -> None:
        if len(body) > 12000:
            raise ValueError('Report exceeds concise GitHub body limit (12000)')

    def update_pull_body(self, owner: str, repo: str, pr_number: int, body: str) -> None:
        self._check_report_body(body)
        self._request('PATCH', f'https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}',
                      json_body={'body': body})

    def upload_report_artifact(self, owner: str, repo: str, content: str) -> str:
        """Isolated orphan commit/ref: never advance the checked document branch.

        Git Data API supports large diagnostics without the Contents API's small
        read limit. A random per-delivery ref retains the immutable artifact.
        Return a link only after GitHub confirms that ref and its exact commit.
        """
        base = f'https://api.github.com/repos/{owner}/{repo}/git'
        def sha(response):
            value = response.get('sha', '') if isinstance(response, dict) else ''
            if not re.fullmatch(r'[0-9a-f]{40}', value):
                raise GitHubAPIError('Artifact upload did not confirm a Git object SHA')
            return value
        blob = sha(self._request('POST', base + '/blobs', json_body={
            'content': base64.b64encode(content.encode('utf-8')).decode('ascii'), 'encoding': 'base64'}))
        tree = sha(self._request('POST', base + '/trees', json_body={'tree': [
            {'path': 'diagnostics.json', 'mode': '100644', 'type': 'blob', 'sha': blob}]}))
        commit = sha(self._request('POST', base + '/commits', json_body={
            'message': 'Documentation review diagnostics', 'tree': tree, 'parents': []}))
        ref = 'refs/heads/ydbdoc-reports/' + uuid.uuid4().hex
        receipt = self._request('POST', base + '/refs', json_body={'ref': ref, 'sha': commit})
        if (not isinstance(receipt, dict) or receipt.get('ref') != ref or
                receipt.get('object', {}).get('sha') != commit):
            raise GitHubAPIError('Artifact upload did not confirm its retention ref')
        return f'https://github.com/{owner}/{repo}/blob/{commit}/diagnostics.json'

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

    def get_branch_sha(self, owner: str, repo: str, branch: str) -> str | None:
        """Return the exact remote head SHA, or ``None`` when absent."""
        encoded = quote(branch, safe="")
        url = f"https://api.github.com/repos/{owner}/{repo}/git/ref/heads/{encoded}"
        try:
            data = self._request("GET", url)
        except GitHubAPIError as exc:
            if exc.status_code == 404:
                return None
            raise
        if not isinstance(data, dict):
            raise GitHubAPIError(f"Malformed branch ref response for {branch}")
        obj = data.get("object")
        sha = str(obj.get("sha") or "") if isinstance(obj, dict) else ""
        if not sha:
            raise GitHubAPIError(f"Branch ref {branch} is missing its object SHA")
        return sha


    def create_pull(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str,
        draft: bool = False,
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
                json_body={
                    "title": title,
                    "head": head,
                    "base": base,
                    "body": body,
                    "draft": draft,
                },
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

    def convert_pull_to_draft(self, owner: str, repo: str, pr_number: int) -> bool:
        """Convert an existing ready-for-review PR to draft via GitHub GraphQL."""
        pull = self.get_pull(owner, repo, pr_number)
        if bool(pull.get("draft")):
            return False
        node_id = str(pull.get("node_id") or "")
        if not node_id:
            raise GitHubAPIError("GitHub pull response has no node_id", status_code=0)
        query = """mutation($id: ID!) {
          convertPullRequestToDraft(input: {pullRequestId: $id}) {
            pullRequest { isDraft }
          }
        }"""
        data = self._request(
            "POST",
            "https://api.github.com/graphql",
            json_body={"query": query, "variables": {"id": node_id}},
        )
        is_draft = bool(
            isinstance(data, dict)
            and data.get("data", {})
            .get("convertPullRequestToDraft", {})
            .get("pullRequest", {})
            .get("isDraft")
        )
        if not is_draft:
            raise GitHubAPIError(
                "GitHub did not convert pull request to draft", status_code=0
            )
        return True
