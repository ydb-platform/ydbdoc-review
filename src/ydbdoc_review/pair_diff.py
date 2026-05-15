"""Heuristics for RU/EN doc pair diffs vs merge-base."""

from __future__ import annotations

import re

_ADDED_LINE = re.compile(r"^\+(?!\+\+)", re.MULTILINE)


def diff_has_added_lines(diff: str | None) -> bool:
    """True if unified diff contains at least one added content line (`+` not `+++`)."""
    if not diff or not diff.strip():
        return False
    return _ADDED_LINE.search(diff) is not None


def pair_needs_en_from_ru_only_diff(
    *,
    ru_path: str,
    ru_diff: str | None,
    en_diff: str | None,
    pr_changed_paths: set[str],
) -> bool:
    """
    PR changed Russian but not English for this pair: EN must be updated from RU diff.

    Used to override check-model false ``aligned=True`` when RU adds sections EN lacks.
    """
    if ru_path not in pr_changed_paths:
        return False
    en_path = ru_path.replace("/ru/", "/en/", 1)
    if en_path in pr_changed_paths and diff_has_added_lines(en_diff):
        return False
    return diff_has_added_lines(ru_diff)
