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
from ydbdoc_review.pipeline.pairs import (
    ChangeKind,
    DocPair,
    NavigationPair,
    build_doc_pairs,
)

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
    merged: bool = False
    state: str = "open"


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
        merged=bool(data.get("merged")),
        state=str(data.get("state") or "open"),
    )


def repo_https_clone_url(owner: str, repo: str) -> str:
    """HTTPS clone URL for the upstream (target) repository."""
    return f"https://github.com/{owner}/{repo}.git"


def is_fork_head(ctx: PullRequestContext) -> bool:
    """True when the source PR head branch lives on a contributor fork."""
    upstream = f"{ctx.owner}/{ctx.repo}".casefold()
    return ctx.head_repo_full_name.casefold() != upstream


def translation_branch_base(ctx: PullRequestContext) -> tuple[str, str]:
    """Remote URL and branch ref to create the translation branch on upstream.

    Fork PRs: branch from upstream ``base_ref`` (e.g. ``main``) — the branch the
    source PR targets / merges into. Contributor feature branches do not exist on
    upstream; basing on the fork head pulls foreign history and can break push.

    Same-repo open PRs: branch from the source PR head on upstream (stacked PR).

    Merged PRs (any repo): branch from ``base_ref`` — the head branch is often
    deleted after merge (e.g. ``alexnick88-patch-1`` on #40070).
    """
    upstream = repo_https_clone_url(ctx.owner, ctx.repo)
    if is_fork_head(ctx) or ctx.merged:
        return upstream, ctx.base_ref
    return upstream, ctx.head_ref


def translation_pr_base(ctx: PullRequestContext) -> str:
    """Base branch for the auto-translation PR opened on upstream."""
    _, base_ref = translation_branch_base(ctx)
    return base_ref


def verify_push_remote_url(ctx: PullRequestContext) -> str:
    """HTTPS remote for ``doc_verify`` repair push when head is pushable.

    Only valid when ``is_fork_head(ctx)`` is False. Callers must take the
    fork-fallback path (``verify_fixup_branch`` + new upstream PR) otherwise.
    """
    return repo_https_clone_url(ctx.owner, ctx.repo)


def verify_fixup_branch(prefix: str, source_pr: int) -> str:
    """Upstream branch name for a ``doc_verify`` fixup PR (fork head fallback)."""
    return f"{prefix}{source_pr}"


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


def source_pr_content_ref(
    gh: GitHubClient, owner: str, repo: str, source_pr: int
) -> tuple[str, str, str]:
    """Git ref for RU files — same tree ``doc_translate`` uses (source PR head).

    Returns ``(repo_owner, repo_name, head_sha)``. Head may live on a fork.
    """
    data = gh.get_pull(owner, repo, source_pr)
    head = data.get("head") or {}
    head_repo = head.get("repo") or {}
    head_owner = head_repo.get("owner") or {}
    ru_owner = str(head_owner.get("login") or owner)
    ru_repo = str(head_repo.get("name") or repo)
    sha = str(head.get("sha") or "")
    if not sha:
        raise ValueError(f"Source PR #{source_pr} has no head sha")
    return ru_owner, ru_repo, sha


def load_verify_pair_contents(
    repo_path: str,
    pairs: list[DocPair],
    *,
    merge_base_with: str,
    gh: GitHubClient,
    owner: str,
    repo: str,
    source_pr: int,
) -> list[PairContent]:
    """Load EN from translation PR checkout; RU from source PR head (not ``main``).

    Translation branches only commit EN paths — RU on disk is the branch base
    (often newer ``main``). QA must compare against the same RU ``doc_translate``
    translated from.
    """
    ru_owner, ru_repo, ru_ref = source_pr_content_ref(gh, owner, repo, source_pr)
    contents: list[PairContent] = []
    for pair in pairs:
        en_text = read_text(repo_path, pair.en_path)
        if en_text is None and not pair.en_deleted:
            en_text = read_text_at_ref(repo_path, "HEAD", pair.en_path)

        ru_text: str | None = None
        if not pair.ru_deleted:
            ru_text = gh.get_file_text(ru_owner, ru_repo, pair.ru_path, ru_ref)

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


def load_verify_navigation_ru_texts(
    pairs: list[NavigationPair],
    *,
    gh: GitHubClient,
    owner: str,
    repo: str,
    source_pr: int,
) -> dict[str, str]:
    """Load RU navigation YAML from source PR head (§6.31)."""
    ru_owner, ru_repo, ru_ref = source_pr_content_ref(gh, owner, repo, source_pr)
    texts: dict[str, str] = {}
    for pair in pairs:
        if pair.ru_deleted:
            continue
        text = gh.get_file_text(ru_owner, ru_repo, pair.ru_path, ru_ref)
        if text is not None:
            texts[pair.ru_path] = text
    return texts


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
