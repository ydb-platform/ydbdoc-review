"""Detect tip RU/EN changes after a source PR (REQUIREMENTS_RU.md §10 / §12).

When tip content diverges from the fixed source-PR SHA, emit yellow warnings
with paths, content ids (blob SHAs), and touching commits. Translate current
tip RU fully and overwrite result EN entirely. Warnings never become
completeness blockers and never skip commit/push.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from typing import Literal

from ydbdoc_review.github.git_ops import read_text_at_ref
from ydbdoc_review.pipeline.analyze import PairContent

TipNewerKind = Literal[
    "ru_modified",
    "ru_created",
    "ru_deleted",
    "en_modified",
    "en_created",
    "en_deleted",
]


@dataclass(frozen=True)
class TipNewerFinding:
    """One tip-vs-source divergence for a docs path."""

    path: str
    kind: TipNewerKind
    source_blob: str | None
    tip_blob: str | None
    commits: tuple[str, ...]


def blob_id_at_ref(repo: str, ref: str, rel_path: str) -> str | None:
    """Return the blob object id for ``ref:path``, or None if absent."""
    path = rel_path.replace("\\", "/")
    proc = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--verify", f"{ref}:{path}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def commits_touching_path(
    repo: str,
    *,
    since_ref: str,
    until_ref: str,
    rel_path: str,
    limit: int = 12,
) -> tuple[str, ...]:
    """Short commit SHAs that touched ``rel_path`` on ``since_ref..until_ref``."""
    path = rel_path.replace("\\", "/")
    proc = subprocess.run(
        [
            "git",
            "-C",
            repo,
            "log",
            "--format=%h",
            f"--max-count={limit}",
            f"{since_ref}..{until_ref}",
            "--",
            path,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return ()
    commits = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    return tuple(commits)


def _short_blob(blob: str | None) -> str:
    if not blob:
        return "absent"
    return blob[:12]


def _kind_label(kind: TipNewerKind) -> str:
    return {
        "ru_modified": "русский файл изменился после исходного pull request",
        "ru_created": "русский файл был создан после исходного pull request",
        "ru_deleted": "русский файл был удалён после исходного pull request",
        "en_modified": "английский файл изменился после исходного pull request",
        "en_created": "английский файл был создан после исходного pull request",
        "en_deleted": "английский файл был удалён после исходного pull request",
    }[kind]


def format_tip_newer_warning(finding: TipNewerFinding) -> str:
    """Human-readable yellow warning (§10 / §12): path, content ids, commits."""
    commits = ", ".join(f"`{c}`" for c in finding.commits) if finding.commits else "—"
    return (
        f"⚠ `{finding.path}`: {_kind_label(finding.kind)}. "
        f"Содержимое source=`{_short_blob(finding.source_blob)}` → "
        f"tip=`{_short_blob(finding.tip_blob)}`; "
        f"затронувшие коммиты: {commits}. "
        "Текущий русский текст переводится целиком; английский результат "
        "перезаписывается целиком (политика overwrite). "
        "Предупреждение не блокирует commit/push."
    )


def _compare_path(
    repo: str,
    *,
    path: str,
    source_ref: str,
    tip_ref: str,
    locale: Literal["ru", "en"],
) -> TipNewerFinding | None:
    source_blob = blob_id_at_ref(repo, source_ref, path)
    tip_blob = blob_id_at_ref(repo, tip_ref, path)
    if source_blob == tip_blob:
        return None
    if source_blob is None and tip_blob is not None:
        kind: TipNewerKind = "ru_created" if locale == "ru" else "en_created"
    elif source_blob is not None and tip_blob is None:
        kind = "ru_deleted" if locale == "ru" else "en_deleted"
    else:
        kind = "ru_modified" if locale == "ru" else "en_modified"
    commits = commits_touching_path(
        repo, since_ref=source_ref, until_ref=tip_ref, rel_path=path
    )
    return TipNewerFinding(
        path=path,
        kind=kind,
        source_blob=source_blob,
        tip_blob=tip_blob,
        commits=commits,
    )


def detect_tip_newer_for_pair(
    repo: str,
    content: PairContent,
    *,
    source_ref: str,
    tip_ref: str,
) -> tuple[TipNewerFinding, ...]:
    """Compare source-PR blobs vs tip for the pair's RU and EN paths."""
    pair = content.pair
    findings: list[TipNewerFinding] = []
    ru = _compare_path(
        repo,
        path=pair.ru_path,
        source_ref=source_ref,
        tip_ref=tip_ref,
        locale="ru",
    )
    if ru is not None:
        findings.append(ru)
    en = _compare_path(
        repo,
        path=pair.en_path,
        source_ref=source_ref,
        tip_ref=tip_ref,
        locale="en",
    )
    if en is not None:
        findings.append(en)
    return tuple(findings)


def tip_newer_warnings_block_publish(_warnings: tuple[str, ...] | list[str]) -> bool:
    """§10: tip-newer yellow warnings never block commit/push."""
    return False


def apply_tip_newer_policy(
    repo: str,
    contents: list[PairContent],
    *,
    source_ref: str | None,
    tip_ref: str,
) -> tuple[list[PairContent], tuple[str, ...]]:
    """Rewrite pair contents for tip-newer overwrite and collect yellow warnings.

    When ``source_ref`` is unset (open / unmerged PR path), returns contents
    unchanged. Otherwise:

    - If tip RU differs from the source-PR blob, use tip RU as ``ru_text``.
    - Mark ``force_full_overwrite`` so EN preserve / stitch paths are skipped.
    - Collect yellow warnings with paths, content ids, and commits.
    """
    if not source_ref:
        return contents, ()

    updated: list[PairContent] = []
    warnings: list[str] = []
    for content in contents:
        findings = detect_tip_newer_for_pair(
            repo, content, source_ref=source_ref, tip_ref=tip_ref
        )
        if not findings:
            updated.append(content)
            continue

        tip_ru = read_text_at_ref(repo, tip_ref, content.pair.ru_path)
        tip_en = read_text_at_ref(repo, tip_ref, content.pair.en_path)
        # Current tip bodies are authoritative for §3/§10 overwrite.
        new_ru = tip_ru if tip_ru is not None else content.ru_text
        new_en = tip_en  # may be None when tip deleted EN after source
        updated.append(
            replace(
                content,
                ru_text=new_ru,
                en_text=new_en,
                force_full_overwrite=True,
                tip_newer_warnings=tuple(format_tip_newer_warning(f) for f in findings),
            )
        )
        warnings.extend(format_tip_newer_warning(f) for f in findings)
    return updated, tuple(warnings)
