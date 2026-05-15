from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import quote

import httpx

from ydbdoc_review import git_local


@dataclass(frozen=True)
class MergedPrRef:
    number: int
    url: str
    title: str
    merged_at: str


@dataclass(frozen=True)
class PathPrerequisiteInfo:
    """Merged PRs that brought RU changes to the base branch, oldest → newest."""

    chain: tuple[MergedPrRef, ...]
    recommended: MergedPrRef | None


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


def compare_branch_url(
    owner: str,
    repo: str,
    base: str,
    head_branch: str,
    *,
    title: str | None = None,
    body: str | None = None,
) -> str:
    from urllib.parse import quote

    url = f"https://github.com/{owner}/{repo}/compare/{base}...{head_branch}?expand=1"
    if title:
        url += f"&title={quote(title)}"
    if body:
        url += f"&body={quote(body)}"
    return url


def find_open_pull_by_head(
    owner: str,
    repo: str,
    *,
    head_branch: str,
    base: str,
    token: str,
) -> tuple[str, int] | None:
    """Return (html_url, number) for an open PR with the given head branch in owner/repo."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    r = httpx.get(
        url,
        headers=_headers(token),
        params={"state": "open", "head": f"{owner}:{head_branch}", "base": base},
        timeout=60.0,
    )
    if r.status_code != 200:
        return None
    pulls = r.json()
    if not isinstance(pulls, list) or not pulls:
        return None
    data = pulls[0]
    html = str(data.get("html_url", ""))
    num = int(data.get("number", 0))
    return (html, num) if html and num else None


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
    existing = find_open_pull_by_head(
        owner, repo, head_branch=head, base=base, token=token
    )
    if existing:
        return existing

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
    if r.status_code == 422:
        existing = find_open_pull_by_head(
            owner, repo, head_branch=head, base=base, token=token
        )
        if existing:
            return existing
    return None


def pull_create_error(
    owner: str,
    repo: str,
    *,
    title: str,
    head: str,
    base: str,
    body: str,
    token: str,
) -> str:
    """Return GitHub API error text from a pull create attempt (for logs)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    r = httpx.post(
        url,
        headers=_headers(token),
        json={"title": title, "head": head, "base": base, "body": body},
        timeout=120.0,
    )
    if r.status_code == 201:
        return ""
    try:
        data = r.json()
        msg = str(data.get("message", ""))
        errs = data.get("errors")
        if isinstance(errs, list) and errs:
            parts = [str(e.get("message", e)) for e in errs[:3]]
            return f"{msg}: {'; '.join(parts)}" if msg else "; ".join(parts)
        return msg or r.text[:500]
    except (json.JSONDecodeError, ValueError, AttributeError):
        return r.text[:500] if r.text else f"HTTP {r.status_code}"


def delete_branch_if_exists(owner: str, repo: str, branch: str, token: str) -> bool:
    """
    Delete ``refs/heads/{branch}`` when present.

    Returns True if a branch was deleted, False if it did not exist.
    Raises on other API errors (permissions, etc.).
    """
    ref = quote(f"heads/{branch}", safe="/")
    url = f"https://api.github.com/repos/{owner}/{repo}/git/refs/{ref}"
    r = httpx.delete(url, headers=_headers(token), timeout=60.0)
    if r.status_code == 204:
        return True
    if r.status_code == 404:
        return False
    r.raise_for_status()
    return False


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


def pr_touches_path(
    owner: str,
    repo: str,
    pr_number: int,
    ru_path: str,
    token: str,
    *,
    _cache: dict[tuple[str, str, int, str], bool] | None = None,
) -> bool:
    """True if `ru_path` (or its rename source) appears in that PR's changed files."""
    key = (owner, repo, pr_number, ru_path)
    if _cache is not None and key in _cache:
        return _cache[key]
    found = False
    for item in iter_pr_files(owner, repo, pr_number, token):
        fn = item.get("filename")
        prev = item.get("previous_filename")
        if fn == ru_path or prev == ru_path:
            found = True
            break
    if _cache is not None:
        _cache[key] = found
    return found


def list_pulls_for_commit(
    owner: str, repo: str, commit_sha: str, token: str
) -> list[dict[str, Any]]:
    """Pull requests associated with a commit (may be empty without merge commit link)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/{commit_sha}/pulls"
    r = httpx.get(url, headers=_headers(token), timeout=60.0)
    if r.status_code in (404, 409):
        return []
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def oldest_commit_for_path(
    owner: str,
    repo: str,
    path: str,
    ref: str,
    token: str,
    *,
    max_pages: int = 20,
) -> str | None:
    """Walk commit history for `path` at `ref`; return SHA of the oldest commit."""
    url = f"https://api.github.com/repos/{owner}/{repo}/commits"
    page = 1
    oldest: str | None = None
    while page <= max_pages:
        r = httpx.get(
            url,
            headers=_headers(token),
            params={"path": path, "sha": ref, "per_page": 100, "page": page},
            timeout=120.0,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        oldest = str(batch[-1].get("sha", "")) or oldest
        if len(batch) < 100:
            break
        page += 1
    return oldest if oldest else None


def _merged_pr_ref_from_pull(pull: dict[str, Any]) -> MergedPrRef | None:
    if not pull.get("merged_at"):
        return None
    num = pull.get("number")
    url = pull.get("html_url")
    if not isinstance(num, int) or not url:
        return None
    return MergedPrRef(
        number=num,
        url=str(url),
        title=str(pull.get("title", "")),
        merged_at=str(pull["merged_at"]),
    )


def list_commit_shas_for_path(
    owner: str,
    repo: str,
    path: str,
    ref: str,
    token: str,
    *,
    max_pages: int = 20,
    max_commits: int = 50,
) -> list[str]:
    """Commit SHAs touching `path` at `ref`, newest first (capped)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/commits"
    page = 1
    shas: list[str] = []
    while page <= max_pages and len(shas) < max_commits:
        r = httpx.get(
            url,
            headers=_headers(token),
            params={"path": path, "sha": ref, "per_page": 100, "page": page},
            timeout=120.0,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for item in batch:
            sha = item.get("sha")
            if isinstance(sha, str) and sha:
                shas.append(sha)
                if len(shas) >= max_commits:
                    break
        if len(batch) < 100:
            break
        page += 1
    return shas


def find_prerequisite_chain_for_path(
    owner: str,
    repo: str,
    ru_path: str,
    *,
    token: str,
    repo_path: str | None,
    base_git_ref: str | None,
    base_branch: str,
    exclude_pr: int | None = None,
    max_commits: int = 50,
) -> PathPrerequisiteInfo:
    """
  Find merged PRs that changed `ru_path` on the base branch (oldest → newest).

  `recommended` is the latest merged PR in the chain (for `doc_translate`), excluding
  `exclude_pr` when set (typically the open PR being labeled).
    """
    shas: list[str] = []
    if repo_path and base_git_ref:
        shas = git_local.commits_touching_path(
            repo_path, base_git_ref, ru_path, max_count=max_commits
        )
    if not shas:
        shas = list_commit_shas_for_path(
            owner,
            repo,
            ru_path,
            base_branch,
            token,
            max_commits=max_commits,
        )

    by_number: dict[int, MergedPrRef] = {}
    touch_cache: dict[tuple[str, str, int, str], bool] = {}
    for sha in shas:
        for pull in list_pulls_for_commit(owner, repo, sha, token):
            ref = _merged_pr_ref_from_pull(pull)
            if ref is None:
                continue
            if not pr_touches_path(
                owner, repo, ref.number, ru_path, token, _cache=touch_cache
            ):
                continue
            prev = by_number.get(ref.number)
            if prev is None or ref.merged_at >= prev.merged_at:
                by_number[ref.number] = ref

    chain = tuple(sorted(by_number.values(), key=lambda r: (r.merged_at, r.number)))
    recommended: MergedPrRef | None = None
    for ref in reversed(chain):
        if exclude_pr is None or ref.number != exclude_pr:
            recommended = ref
            break
    if recommended is None and chain:
        recommended = chain[-1]
    return PathPrerequisiteInfo(chain=chain, recommended=recommended)


def find_introducing_pull_for_path(
    owner: str,
    repo: str,
    ru_path: str,
    *,
    token: str,
    repo_path: str | None,
    base_git_ref: str | None,
    base_branch: str,
    exclude_pr: int | None = None,
) -> tuple[int, str, str] | None:
    """Backward-compatible: latest merged PR in the prerequisite chain."""
    info = find_prerequisite_chain_for_path(
        owner,
        repo,
        ru_path,
        token=token,
        repo_path=repo_path,
        base_git_ref=base_git_ref,
        base_branch=base_branch,
        exclude_pr=exclude_pr,
    )
    rec = info.recommended
    if rec is None:
        return None
    return rec.number, rec.url, rec.title
