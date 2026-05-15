from __future__ import annotations

import base64
from typing import Any, Iterator
from urllib.parse import quote

import httpx


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_pull(owner: str, repo: str, pr: int, token: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr}"
    r = httpx.get(url, headers=_headers(token), timeout=60.0)
    r.raise_for_status()
    return r.json()


def iter_pr_files(owner: str, repo: str, pr: int, token: str) -> Iterator[dict[str, Any]]:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr}/files"
    page = 1
    while True:
        r = httpx.get(
            url,
            headers=_headers(token),
            params={"per_page": 100, "page": page},
            timeout=120.0,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            return
        for item in batch:
            yield item
        if len(batch) < 100:
            return
        page += 1


def create_pull(
    owner: str,
    repo: str,
    *,
    title: str,
    head: str,
    base: str,
    body: str,
    token: str,
) -> tuple[str, int] | None:
    """
    Open a pull request in owner/repo (usually the PR head fork).
    head / base are branch names in that same repository.
    Returns (html_url, number) or None if GitHub rejected the request.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    r = httpx.post(
        url,
        headers=_headers(token),
        json={"title": title, "head": head, "base": base, "body": body},
        timeout=120.0,
    )
    if r.status_code == 201:
        data = r.json()
        html = str(data.get("html_url", ""))
        num = int(data.get("number", 0))
        return (html, num) if html and num else None
    return None


def post_issue_comment(owner: str, repo: str, pr: int, body: str, token: str) -> str:
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr}/comments"
    r = httpx.post(
        url,
        headers=_headers(token),
        json={"body": body},
        timeout=60.0,
    )
    r.raise_for_status()
    data = r.json()
    return str(data.get("html_url", ""))


def get_file_text(
    owner: str,
    repo: str,
    path: str,
    ref: str,
    token: str,
) -> str | None:
    """Return file UTF-8 text at ref, or None if missing / not a file."""
    enc_path = quote(path, safe="/")
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{enc_path}"
    r = httpx.get(
        url,
        headers=_headers(token),
        params={"ref": ref},
        timeout=120.0,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return None
    if data.get("encoding") != "base64" or "content" not in data:
        return None
    raw = base64.b64decode(data["content"].replace("\n", ""))
    return raw.decode("utf-8")


def head_repo_from_pr(pr: dict[str, Any]) -> tuple[str, str, str, str]:
    """
    Returns (head_owner, head_repo_name, head_sha, head_ref).
    """
    head = pr["head"]
    repo = head["repo"]
    if repo is None:
        raise SystemExit("PR head repository is unavailable (deleted branch?).")
    owner = repo["owner"]["login"]
    name = repo["name"]
    sha = head["sha"]
    ref = head["ref"]
    return owner, name, sha, ref


def base_repo_from_pr(pr: dict[str, Any]) -> tuple[str, str]:
    base = pr["base"]["repo"]
    return base["owner"]["login"], base["name"]


def base_ref_from_pr(pr: dict[str, Any]) -> str:
    return str(pr["base"]["ref"])


def base_clone_url_from_pr(pr: dict[str, Any]) -> str:
    return str(pr["base"]["repo"]["clone_url"])


def is_fork_pr(pr: dict[str, Any]) -> bool:
    head = pr["head"]["repo"]
    base = pr["base"]["repo"]
    if head is None or base is None:
        return False
    return (
        head["owner"]["login"] != base["owner"]["login"]
        or head["name"] != base["name"]
    )


def pr_is_merged(pr: dict[str, Any]) -> bool:
    return bool(pr.get("merged_at"))
