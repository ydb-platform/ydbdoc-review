"""Immutable candidate identity and an internal read-only review boundary."""

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class FinalCandidate:
    commit_sha: str
    tree_sha: str
    en_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]


@dataclass(frozen=True)
class CandidateReviewReceipt:
    candidate_sha: str
    candidate_tree_sha: str


def _path(path: str) -> str:
    normalized = PurePosixPath(path.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts or str(normalized) == ".":
        raise ValueError("invalid_candidate_path")
    return str(normalized)


def _git(repo_path: str, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", repo_path, *args], stderr=subprocess.PIPE)


def bind_final_candidate(
    repo_path: str, *, candidate_sha: str,
    en_paths: tuple[str, ...], deleted_paths: tuple[str, ...],
) -> FinalCandidate:
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", candidate_sha):
        raise ValueError("candidate_requires_full_commit_sha")
    if _git(repo_path, "cat-file", "-t", candidate_sha).strip() != b"commit":
        raise ValueError("candidate_requires_commit")
    tree = _git(repo_path, "rev-parse", f"{candidate_sha}^{{tree}}").decode("ascii").strip()
    paths = tuple(sorted({_path(path) for path in en_paths}))
    deleted = tuple(sorted({_path(path) for path in deleted_paths}))
    if set(paths) & set(deleted):
        raise ValueError("candidate_path_is_also_deleted")
    return FinalCandidate(candidate_sha, tree, paths, deleted)


def read_candidate_bytes(repo_path: str, candidate: FinalCandidate, path: str) -> bytes | None:
    path = _path(path)
    entries = _git(repo_path, "ls-tree", "--full-tree", "-z", candidate.commit_sha, "--", path)
    if not entries:
        return None
    return _git(repo_path, "cat-file", "blob", f"{candidate.commit_sha}:{path}")


def review_final_candidate(
    candidate: FinalCandidate, review: Callable[[FinalCandidate], None],
) -> CandidateReviewReceipt:
    review(candidate)
    return CandidateReviewReceipt(candidate.commit_sha, candidate.tree_sha)


def require_reviewed_candidate(candidate: FinalCandidate, receipt: CandidateReviewReceipt) -> None:
    if (candidate.commit_sha, candidate.tree_sha) != (receipt.candidate_sha, receipt.candidate_tree_sha):
        raise ValueError("candidate_review_mismatch")
