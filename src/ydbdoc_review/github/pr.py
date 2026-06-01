"""Pull request helpers: file changes, pair loading, PR context."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.github.git_ops import (
    file_diff_range,
    list_local_changes,
    read_text,
    read_text_at_ref,
)
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.pairs import ChangeKind, DocPair, build_doc_pairs

_STATUS_TO_KIND: dict[str, ChangeKind] = {
    "added": "added",
    "modified": "modified",
    "removed": "deleted",
    "deleted": "deleted",
    "renamed": "modified",
    "changed": "modified",
}


def parse_repo(full_name: str) -> tuple[str, str]:
    """Split ``owner/name``."""
    parts = full_name.strip().split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid repository name: {full_name!r}")
    return parts[0], parts[1]


@dataclass(frozen=True)
class PullRequestContext:
    """Metadata needed for branch / PR operations."""

    owner: str
    repo: str
    number: int
    title: str
    head_ref: str
    head_sha: str
    head_repo_full_name: str
    head_repo_https_url: str
    base_ref: str


def pull_request_context(
    client: GitHubClient, owner: str, repo: str, pr_number: int
) -> PullRequestContext:
    data = client.get_pull(owner, repo, pr_number)
    head = data.get("head") or {}
    head_repo = head.get("repo") or {}
    base = data.get("base") or {}
    clone_url = str(head_repo.get("clone_url") or "")
    if not clone_url:
        raise ValueError(f"PR #{pr_number} missing head repo clone URL")
    return PullRequestContext(
        owner=owner,
        repo=repo,
        number=pr_number,
        title=str(data.get("title") or ""),
        head_ref=str(head.get("ref") or ""),
        head_sha=str(head.get("sha") or ""),
        head_repo_full_name=str(head_repo.get("full_name") or f"{owner}/{repo}"),
        head_repo_https_url=clone_url,
        base_ref=str(base.get("ref") or ""),
    )


def list_pr_file_changes_api(
    client: GitHubClient, owner: str, repo: str, pr_number: int
) -> list[tuple[str, ChangeKind]]:
    """Changed paths from the GitHub PR files API."""
    out: list[tuple[str, ChangeKind]] = []
    for item in client.iter_pull_files(owner, repo, pr_number):
        filename = str(item.get("filename") or "").replace("\\", "/")
        if not filename:
            continue
        status = str(item.get("status") or "modified")
        kind = _STATUS_TO_KIND.get(status, "modified")
        out.append((filename, kind))
    return out


def list_pr_file_changes_git(
    repo_path: str, merge_base_with: str
) -> list[tuple[str, ChangeKind]]:
    """Changed paths from local git merge-base diff."""
    return list_local_changes(repo_path, merge_base_with)


def source_pr_number_from_branch(branch: str, *, prefix: str) -> int | None:
    """Extract source PR number from ``ydbdoc-review/pr-<N>``."""
    if not branch.startswith(prefix):
        return None
    suffix = branch[len(prefix) :]
    if suffix.isdigit():
        return int(suffix)
    return None


def load_pair_contents(
    repo_path: str,
    pairs: list[DocPair],
    *,
    merge_base_with: str,
) -> list[PairContent]:
    """Load RU/EN bodies and diffs for each pair from the local checkout."""
    contents: list[PairContent] = []
    for pair in pairs:
        ru_text = read_text(repo_path, pair.ru_path)
        en_text = read_text(repo_path, pair.en_path)
        if ru_text is None and not pair.ru_deleted:
            ru_text = read_text_at_ref(repo_path, "HEAD", pair.ru_path)
        if en_text is None and not pair.en_deleted:
            en_text = read_text_at_ref(repo_path, "HEAD", pair.en_path)

        ru_diff = (
            file_diff_range(repo_path, merge_base_with, pair.ru_path)
            if pair.ru_changed
            else None
        )
        en_diff = (
            file_diff_range(repo_path, merge_base_with, pair.en_path)
            if pair.en_changed
            else None
        )
        contents.append(
            PairContent(
                pair=pair,
                ru_text=ru_text,
                en_text=en_text,
                ru_diff_vs_base=ru_diff or None,
                en_diff_vs_base=en_diff or None,
            )
        )
    return contents


def build_pairs_from_changes(
    changes: list[tuple[str, ChangeKind]], *, docs_root: str
) -> list[DocPair]:
    return build_doc_pairs(changes, docs_root=docs_root)


_BRANCH_SOURCE_RE = re.compile(
    r"ydbdoc-review/pr-(\d+)", re.IGNORECASE
)


def parse_source_pr_from_text(text: str) -> int | None:
    """Find source PR number in translation PR title/body."""
    match = _BRANCH_SOURCE_RE.search(text)
    if match:
        return int(match.group(1))
    match = re.search(r"PR\s*#(\d+)", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None
