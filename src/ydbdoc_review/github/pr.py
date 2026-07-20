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
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.validation.autotitle_hrefs import overlay_autotitle_fragment_hrefs
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.pairs import (
    ChangeKind,
    DocPair,
    NavigationPair,
    build_doc_pairs,
)
from ydbdoc_review.navigation.toc import parse_toc_items
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation

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
    merge_commit_sha: str | None = None


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
    merge_sha = str(data.get("merge_commit_sha") or "") or None
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
        merge_commit_sha=merge_sha,
    )


def translate_ru_content_ref(ctx: PullRequestContext) -> str | None:
    """Git ref for RU bodies during ``doc_translate``.

    Open PRs: checkout HEAD (caller default). Merged PRs: ``merge_commit_sha`` —
    the tree that landed on the base branch. Feature ``head.sha`` can lag behind
    squash/rebase resolution and regress EN (e.g. #43010 → #47100 YFM010).
    """
    if ctx.merged and ctx.merge_commit_sha:
        return ctx.merge_commit_sha
    return None


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
    """HTTPS remote for upstream ``doc_verify`` fixup pushes."""
    return repo_https_clone_url(ctx.owner, ctx.repo)


def verify_fixup_branch(prefix: str, source_pr: int) -> str:
    """Upstream branch name for a ``doc_verify`` critic-fixup PR."""
    return f"{prefix}{source_pr}"


def verify_fixup_pr_base(ctx: PullRequestContext, *, translation_branch_prefix: str) -> str:
    """Base branch for the critic-fixup PR opened after ``doc_verify``.

    Translation PR on upstream: target the translation branch so fixes merge there,
    not into the author's feature branch. All other PRs: target ``ctx.base_ref``.
    """
    if source_pr_number_from_branch(ctx.head_ref, prefix=translation_branch_prefix):
        return ctx.head_ref
    return ctx.base_ref


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


_KIND_PRIORITY: dict[ChangeKind, int] = {
    "added": 3,
    "deleted": 2,
    "modified": 1,
}


def merge_pr_file_changes(
    *change_lists: list[tuple[str, ChangeKind]],
) -> list[tuple[str, ChangeKind]]:
    """Union PR file change lists; prefer stronger kinds on duplicate paths (§6.80)."""
    merged: dict[str, ChangeKind] = {}
    for changes in change_lists:
        for raw_path, kind in changes:
            path = raw_path.replace("\\", "/")
            if path not in merged or _KIND_PRIORITY[kind] > _KIND_PRIORITY[merged[path]]:
                merged[path] = kind
    return sorted(merged.items())


def source_pr_number_from_branch(branch: str, *, prefix: str) -> int | None:
    """Extract source PR number from ``ydbdoc-review/pr-<N>``."""
    if not branch.startswith(prefix):
        return None
    suffix = branch[len(prefix) :]
    if suffix.isdigit():
        return int(suffix)
    return None


def is_translation_pr_branch(branch: str, *, translation_branch_prefix: str) -> bool:
    """True when ``doc_verify`` runs on an auto-translation PR head branch."""
    return source_pr_number_from_branch(branch, prefix=translation_branch_prefix) is not None


def source_pr_merged(data: dict) -> bool:
    """True when the source PR is merged into its base branch."""
    return bool(data.get("merged"))


def _source_pr_head_ref(
    data: dict, owner: str, repo: str, source_pr: int
) -> tuple[str, str, str]:
    head = data.get("head") or {}
    head_repo = head.get("repo") or {}
    head_owner = head_repo.get("owner") or {}
    ru_owner = str(head_owner.get("login") or owner)
    ru_repo = str(head_repo.get("name") or repo)
    sha = str(head.get("sha") or "")
    if not sha:
        raise ValueError(f"Source PR #{source_pr} has no head sha")
    return ru_owner, ru_repo, sha


def source_pr_content_ref_from_pull(
    data: dict, owner: str, repo: str, source_pr: int
) -> tuple[str, str, str]:
    """Primary RU git ref: source PR **head** (fork head when applicable, §6.31).

    ``doc_translate`` checks out the labeled PR head; verify must start from the
    same tree. For merged PRs, ``load_verify_pair_contents`` also loads the merge
    commit as an alternate candidate (§6.109).
    """
    return _source_pr_head_ref(data, owner, repo, source_pr)


def source_pr_merge_ref_from_pull(
    data: dict, owner: str, repo: str
) -> tuple[str, str, str] | None:
    """Upstream merge-commit ref for a merged source PR, or ``None``."""
    if not source_pr_merged(data):
        return None
    merge_sha = str(data.get("merge_commit_sha") or "")
    if not merge_sha:
        return None
    return owner, repo, merge_sha


def source_pr_content_ref(
    gh: GitHubClient, owner: str, repo: str, source_pr: int
) -> tuple[str, str, str]:
    """Git ref for RU files — source PR head (same tree ``doc_translate`` uses).

    Returns ``(repo_owner, repo_name, head_sha)``.
    """
    return source_pr_content_ref_from_pull(
        gh.get_pull(owner, repo, source_pr), owner, repo, source_pr
    )


def _markdown_segment_count(text: str | None) -> int | None:
    if not isinstance(text, str):
        return None
    normalized = normalize_ru_source_for_translation(text)
    return len(extract_segments(parse_markdown(normalized)))


def _fence_body_copy_warnings(source_ru: str, en_text: str) -> int:
    from ydbdoc_review.validation.fence_integrity import check_fence_body_copy

    return len(check_fence_body_copy(source_ru, en_text, source_lang="ru"))


def pick_verify_ru_text(
    *,
    en_text: str | None,
    ru_api: str | None = None,
    ru_local: str | None = None,
    ru_merge: str | None = None,
    source_pr_merged: bool = False,
) -> str | None:
    """Choose RU authority for ``doc_verify`` segment alignment (§6.70/§6.106/§6.109).

    Candidates (in preference order for ties): ``ru_api`` (PR head), ``ru_merge``
    (merge commit when present), ``ru_local`` (translation-branch checkout).

    1. Prefer candidates whose segment count matches EN.
    2. Among matches that differ in content, pick fewer ``fence_body_copy``
       warnings (§6.106).
    3. Remaining ties: prefer later candidates when ``source_pr_merged`` (checkout /
       merge closer to current ``main``), else keep earlier (head).
    """
    candidates: list[str] = []
    for text in (ru_api, ru_merge, ru_local):
        if isinstance(text, str) and text not in candidates:
            candidates.append(text)
    if not candidates:
        return None
    if en_text is None:
        return candidates[0]

    en_n = _markdown_segment_count(en_text)
    if en_n is None:
        return candidates[0]

    matching = [c for c in candidates if _markdown_segment_count(c) == en_n]
    if not matching:
        return candidates[0]
    if len(matching) == 1:
        return matching[0]

    scored = [(_fence_body_copy_warnings(c, en_text), i, c) for i, c in enumerate(matching)]
    scored.sort(key=lambda row: (row[0], -row[1] if source_pr_merged else row[1]))
    return scored[0][2]


def _toc_labels(yaml_text: str) -> set[str]:
    items = parse_toc_items(yaml_text)
    labels: set[str] = set()
    for it in items:
        if it.get("href"):
            labels.add(f"href:{it['href']}")
        if it.get("include_path"):
            labels.add(f"include:{it['include_path']}")
    return labels


def pick_verify_nav_ru_text(
    *,
    en_text: str | None,
    ru_api: str | None,
    ru_local: str | None,
) -> str | None:
    """Prefer checkout RU nav when EN entries match main RU but not source PR (§6.70)."""
    if ru_api is None and ru_local is None:
        return None
    if not isinstance(en_text, str):
        return ru_api if isinstance(ru_api, str) else ru_local

    en_labels = _toc_labels(en_text)
    if isinstance(ru_api, str) and isinstance(ru_local, str):
        api_labels = _toc_labels(ru_api)
        local_labels = _toc_labels(ru_local)
        extra_needed = en_labels - api_labels
        if extra_needed and extra_needed <= local_labels:
            return ru_local
    if isinstance(ru_api, str):
        return ru_api
    if isinstance(ru_local, str):
        return ru_local
    return None


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
    """Load EN from translation PR checkout; RU from PR head / merge / checkout.

    Translation branches commit EN only; checkout RU is often the branch base
    (``main``). Always fetch source PR **head** (§6.31 — same tree as
    ``doc_translate`` checkout). For merged PRs also fetch **merge commit** as
    an alternate. ``pick_verify_ru_text`` chooses among head / merge / local by
    EN segment parity and fence-body fit (§6.70 / §6.106 / §6.109).
    """
    pull_data = gh.get_pull(owner, repo, source_pr)
    merged = source_pr_merged(pull_data)
    ru_owner, ru_repo, ru_ref = source_pr_content_ref_from_pull(
        pull_data, owner, repo, source_pr
    )
    merge_ref = source_pr_merge_ref_from_pull(pull_data, owner, repo)
    contents: list[PairContent] = []
    for pair in pairs:
        en_text = read_text(repo_path, pair.en_path)
        if en_text is None and not pair.en_deleted:
            en_text = read_text_at_ref(repo_path, "HEAD", pair.en_path)

        ru_api: str | None = None
        ru_merge: str | None = None
        if not pair.ru_deleted:
            ru_api = gh.get_file_text(ru_owner, ru_repo, pair.ru_path, ru_ref)
            if merge_ref is not None:
                m_owner, m_repo, m_sha = merge_ref
                if (m_owner, m_repo, m_sha) != (ru_owner, ru_repo, ru_ref):
                    ru_merge = gh.get_file_text(m_owner, m_repo, pair.ru_path, m_sha)

        ru_local: str | None = None
        if not pair.ru_deleted:
            ru_local = read_text(repo_path, pair.ru_path)
            if ru_local is None:
                ru_local = read_text_at_ref(repo_path, "HEAD", pair.ru_path)

        ru_text = pick_verify_ru_text(
            en_text=en_text,
            ru_api=ru_api,
            ru_merge=ru_merge,
            ru_local=ru_local,
            source_pr_merged=merged,
        )

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
    ru_content_ref: str | None = None,
) -> list[PairContent]:
    """Load RU/EN bodies and diffs for each pair from the local checkout.

    When ``ru_content_ref`` is set (merged source PR → merge commit, §6.120),
    RU bodies are read from that ref first so stale PR-head trees cannot
    regress already-fixed links on the base branch. Unique ``#fragment``
    autotitle hrefs are then overlaid from the checkout RU (usually ``main``)
    so post-merge moves (Sessions → ``execution_process.md``, §6.128) still win.
    """
    contents: list[PairContent] = []
    for pair in pairs:
        ru_text: str | None = None
        if ru_content_ref:
            ru_text = read_text_at_ref(repo_path, ru_content_ref, pair.ru_path)
        if ru_text is None:
            ru_text = read_text(repo_path, pair.ru_path)
        if ru_text is None and not pair.ru_deleted:
            ru_text = read_text_at_ref(repo_path, "HEAD", pair.ru_path)
        if ru_content_ref and ru_text is not None:
            # Prefer post-merge main fragment targets over stale merge-commit ones
            # (§6.128). Checkout may still be the source PR head — use merge base.
            ru_main = read_text_at_ref(repo_path, merge_base_with, pair.ru_path)
            if ru_main is None:
                ru_main = read_text(repo_path, pair.ru_path)
            if ru_main:
                ru_text = overlay_autotitle_fragment_hrefs(ru_text, ru_main)
        en_text = read_text(repo_path, pair.en_path)
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
    repo_path: str,
    gh: GitHubClient,
    owner: str,
    repo: str,
    source_pr: int,
) -> dict[str, str]:
    """Load RU navigation YAML for ``doc_verify`` (source PR head or checkout)."""
    ru_owner, ru_repo, ru_ref = source_pr_content_ref(gh, owner, repo, source_pr)
    texts: dict[str, str] = {}
    for pair in pairs:
        if pair.ru_deleted:
            continue
        ru_api = gh.get_file_text(ru_owner, ru_repo, pair.ru_path, ru_ref)
        ru_local = read_text(repo_path, pair.ru_path)
        if ru_local is None:
            ru_local = read_text_at_ref(repo_path, "HEAD", pair.ru_path)
        en_text = read_text(repo_path, pair.en_path)
        if en_text is None:
            en_text = read_text_at_ref(repo_path, "HEAD", pair.en_path)
        picked = pick_verify_nav_ru_text(
            en_text=en_text, ru_api=ru_api, ru_local=ru_local
        )
        if picked is not None:
            texts[pair.ru_path] = picked
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
