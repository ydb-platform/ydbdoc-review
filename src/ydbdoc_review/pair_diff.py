"""Heuristics for RU/EN doc pair diffs vs merge-base."""

from __future__ import annotations

import re

_ADDED_LINE = re.compile(r"^\+(?!\+\+)", re.MULTILINE)
_REMOVED_LINE = re.compile(r"^-(?!\-\-)", re.MULTILINE)


def diff_has_added_lines(diff: str | None) -> bool:
    """True if unified diff contains at least one added content line (`+` not `+++`)."""
    if not diff or not diff.strip():
        return False
    return _ADDED_LINE.search(diff) is not None


def diff_has_content_changes(diff: str | None) -> bool:
    """True if unified diff has added or removed content lines (not hunk headers)."""
    if not diff or not diff.strip():
        return False
    return (
        _ADDED_LINE.search(diff) is not None
        or _REMOVED_LINE.search(diff) is not None
    )


def pair_requires_en_translation(
    *,
    ru_path: str,
    en_path: str,
    ru_diff: str | None,
    en_diff: str | None,
    pr_changed_paths: set[str],
) -> bool:
    """
  True when this PR requires an EN update for a changed RU doc.

  - RU in the PR file list but EN is not → always translate (works without git diffs).
  - RU diff has content changes and EN diff in this PR does not → translate.
    """
    if ru_path not in pr_changed_paths:
        return False
    expected_en = ru_path.replace("/ru/", "/en/", 1)
    if en_path != expected_en:
        return False
    if en_path not in pr_changed_paths:
        return True
    if diff_has_content_changes(ru_diff) and not diff_has_content_changes(en_diff):
        return True
    return False


def pair_needs_en_from_ru_only_diff(
    *,
    ru_path: str,
    ru_diff: str | None,
    en_diff: str | None,
    pr_changed_paths: set[str],
) -> bool:
    """Backward-compatible alias; prefer :func:`pair_requires_en_translation`."""
    en_path = ru_path.replace("/ru/", "/en/", 1)
    return pair_requires_en_translation(
        ru_path=ru_path,
        en_path=en_path,
        ru_diff=ru_diff,
        en_diff=en_diff,
        pr_changed_paths=pr_changed_paths,
    )
