"""Scoped translation: only ### sections touched by a small RU PR diff."""

from __future__ import annotations

import re
from dataclasses import dataclass

_HUNK_NEW_RE = re.compile(r"^@@ [^@]* \+(\d+)(?:,(\d+))? @@")

# Max changed lines (add+del) or fraction of file to use section-scoped translate.
_MAX_DIFF_LINES = 120
_MAX_DIFF_FRACTION = 0.15


@dataclass(frozen=True)
class TranslateScope:
    mode: str  # "full" | "sections"
    changed_h3: frozenset[int]


def _h3_index_by_line(text: str) -> list[int]:
    """Line index (0-based) → current ### section number (0 = preamble)."""
    cur = 0
    out: list[int] = []
    for line in text.splitlines():
        if line.startswith("### ") and not line.startswith("#### "):
            cur += 1
        out.append(cur)
    return out


def h3_indices_touched_by_diff(diff: str, ru_text: str) -> set[int]:
    """### section indices (1-based) that appear in a unified diff hunk."""
    if not diff or not diff.strip():
        return set()
    line_h3 = _h3_index_by_line(ru_text)
    if not line_h3:
        return set()
    touched: set[int] = set()
    new_line = 0
    for dline in diff.splitlines():
        if dline.startswith("@@"):
            m = _HUNK_NEW_RE.match(dline)
            if m:
                new_line = int(m.group(1)) - 1
            continue
        if dline.startswith("+++") or dline.startswith("---"):
            continue
        if dline.startswith("\\"):
            continue
        if dline.startswith("+"):
            idx = new_line
            if 0 <= idx < len(line_h3) and line_h3[idx] > 0:
                touched.add(line_h3[idx])
            new_line += 1
        elif dline.startswith("-"):
            idx = new_line
            if 0 <= idx < len(line_h3) and line_h3[idx] > 0:
                touched.add(line_h3[idx])
        elif dline.startswith(" "):
            new_line += 1
    return touched


def _diff_change_stats(diff: str) -> int:
    return sum(
        1
        for line in diff.splitlines()
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith("+++")
        and not line.startswith("---")
    )


def _en_is_close_to_ru(ru_text: str, en_text: str) -> bool:
    from ydbdoc_review.heuristics import (
        _check_file_length_mismatch,
        _check_heading_count_mismatch,
    )

    if _check_heading_count_mismatch(source=ru_text, translation=en_text) is not None:
        return False
    if _check_file_length_mismatch(source=ru_text, translation=en_text) is not None:
        return False
    return True


def compute_translate_scope(
    *,
    ru_text: str,
    en_on_main: str | None,
    ru_pr_diff: str | None,
) -> TranslateScope:
    """Decide full-file vs ###-section scoped translation."""
    if not en_on_main or not en_on_main.strip():
        return TranslateScope(mode="full", changed_h3=frozenset())
    if not ru_pr_diff or not ru_pr_diff.strip():
        return TranslateScope(mode="full", changed_h3=frozenset())
    if not _en_is_close_to_ru(ru_text, en_on_main):
        return TranslateScope(mode="full", changed_h3=frozenset())

    changed = h3_indices_touched_by_diff(ru_pr_diff, ru_text)
    if not changed:
        return TranslateScope(mode="full", changed_h3=frozenset())

    stats = _diff_change_stats(ru_pr_diff)
    ru_lines = max(len(ru_text.splitlines()), 1)
    if stats > _MAX_DIFF_LINES or stats / ru_lines > _MAX_DIFF_FRACTION:
        return TranslateScope(mode="full", changed_h3=frozenset())

    return TranslateScope(mode="sections", changed_h3=frozenset(changed))


def h3_from_unit_label(label: str) -> int | None:
    m = re.search(r"/h3-(\d+)/", label)
    return int(m.group(1)) if m else None
