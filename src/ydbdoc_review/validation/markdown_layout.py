"""Markdown layout fixes for generated EN docs (markdownlint MD031)."""

from __future__ import annotations

import re

_FENCE_LINE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")


def _is_closing_fence_line(line: str) -> bool:
    m = _FENCE_LINE.match(line)
    return m is not None and m.group(3).strip() == ""


def _is_opening_fence_line(line: str) -> bool:
    """Opening fence has info string after backticks (e.g. ```yaml)."""
    m = _FENCE_LINE.match(line)
    if m is None:
        return False
    return m.group(3).strip() != ""


def fix_blanks_around_fences(text: str) -> str:
    """Ensure blank lines before/after fenced code blocks (MD031)."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return text

    out: list[str] = []
    for i, line in enumerate(lines):
        if i > 0 and _is_opening_fence_line(line) and out:
            prev = out[-1]
            if prev.strip() != "":
                out.append("\n")
        out.append(line)
        if _is_closing_fence_line(line) and i + 1 < len(lines):
            nxt = lines[i + 1]
            if nxt.strip() != "":
                out.append("\n")
    return "".join(out)
