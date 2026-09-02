"""Immutable source/publication provenance guard for translation jobs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class ProvenanceFinding:
    category: str
    reason: str
    ru_path: str
    en_path: str
    baseline_ru_oid: str | None
    current_ru_oid: str | None
    baseline_en_oid: str | None
    current_en_oid: str | None
    touching_commits: tuple[str, ...]


BLOCKING_PROVENANCE_REASONS = frozenset({
    "history_diverged",
    "source_pr_en_conflict",
})


def partition_provenance_findings(
    findings: tuple[ProvenanceFinding, ...],
) -> tuple[tuple[ProvenanceFinding, ...], tuple[ProvenanceFinding, ...]]:
    """Split hard provenance failures from stale-source warnings."""
    blocking = tuple(f for f in findings if f.reason in BLOCKING_PROVENANCE_REASONS)
    warnings = tuple(f for f in findings if f.reason not in BLOCKING_PROVENANCE_REASONS)
    return blocking, warnings


def _git(repo: str, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=check, capture_output=True, text=True
    )
    return result.stdout.strip()


def resolve_sha(repo_path: str, ref: str) -> str:
    """Pin a moving ref once."""
    return _git(repo_path, "rev-parse", ref)


def _oid(repo: str, sha: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", f"{sha}:{path}"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or None if result.returncode == 0 else None


def _commits(repo: str, old: str, new: str, paths: tuple[str, str]) -> tuple[str, ...]:
    output = _git(repo, "log", "--format=%H", f"{old}..{new}", "--", *paths)
    return tuple(output.splitlines()) if output else ()


def guard_publication_provenance(
    *,
    repo_path: str,
    merged: bool,
    source_tree_sha: str,
    source_base_sha: str,
    publication_tree_sha: str,
    initial_ru_paths: set[str],
    auto_added_ru_paths: set[str],
    source_pr_paths: set[str],
    to_en_path,
) -> tuple[ProvenanceFinding, ...]:
    ancestor = source_tree_sha if merged else source_base_sha
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, publication_tree_sha],
        cwd=repo_path,
        check=False,
    ).returncode:
        return (
            ProvenanceFinding(
                "translation_provenance",
                "history_diverged",
                "",
                "",
                ancestor,
                publication_tree_sha,
                None,
                None,
                (),
            ),
        )
    findings: list[ProvenanceFinding] = []
    for ru_path in sorted(initial_ru_paths | auto_added_ru_paths):
        en_path = to_en_path(ru_path)
        ru_baseline_sha = source_tree_sha if merged or ru_path in auto_added_ru_paths else source_base_sha
        base_ru = _oid(repo_path, ru_baseline_sha, ru_path)
        current_ru = _oid(repo_path, publication_tree_sha, ru_path)
        base_en = _oid(repo_path, source_base_sha, en_path)
        current_en = _oid(repo_path, publication_tree_sha, en_path)
        reason = None
        if en_path in source_pr_paths:
            reason = "source_pr_en_conflict"
        elif base_ru != current_ru:
            reason = "newer_ru"
        elif base_en != current_en:
            if base_en is None:
                reason = "en_created"
            elif current_en is None:
                reason = "en_deleted"
            else:
                reason = "newer_en"
        if reason:
            findings.append(
                ProvenanceFinding(
                    "stale_source_or_newer_translation",
                    reason,
                    ru_path,
                    en_path,
                    base_ru,
                    current_ru,
                    base_en,
                    current_en,
                    _commits(repo_path, source_base_sha, publication_tree_sha, (ru_path, en_path)),
                )
            )
    return tuple(findings)
