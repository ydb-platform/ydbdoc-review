"""Split YDB markdown articles at ``##`` headings (respecting fences and tab blocks)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_HUNK_HEADER_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@", re.MULTILINE)


@dataclass(frozen=True)
class MarkdownSection:
    index: int
    heading: str
    """Full section text (``##`` line and body, or preamble before the first ``##``)."""

    content: str
    start_line: int
    end_line: int


def _is_fence_toggle(line: str) -> bool:
    s = line.strip()
    return s.startswith("```") and len(s) >= 3


def _h2_heading(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("##") and not s.startswith("###")


def _tabs_block_start(line: str) -> bool:
    s = line.strip().lower()
    return "{% list tabs" in s or "{% list tabsgroup" in s


def _tabs_block_end(line: str) -> bool:
    s = line.strip().lower()
    return "{% endlist %}" in s or "{% endtabs %}" in s


def split_markdown_sections(text: str) -> list[MarkdownSection]:
    if not text.strip():
        return [MarkdownSection(0, "", "", 1, 1)]

    lines = text.split("\n")
    groups: list[list[str]] = []
    cur: list[str] = []
    in_fence = False
    in_tabs = False

    for line in lines:
        if _tabs_block_start(line):
            in_tabs = True
        if in_tabs and _tabs_block_end(line):
            in_tabs = False
        if _is_fence_toggle(line):
            in_fence = not in_fence
        if _h2_heading(line) and not in_fence and not in_tabs and cur:
            groups.append(cur)
            cur = []
        cur.append(line)
    if cur:
        groups.append(cur)

    sections: list[MarkdownSection] = []
    line_no = 1
    for idx, grp in enumerate(groups):
        content = "\n".join(grp)
        n_lines = len(grp) if grp else 1
        start = line_no
        end = line_no + n_lines - 1
        line_no = end + 1
        heading = ""
        for ln in grp:
            if _h2_heading(ln):
                heading = ln.strip()
                break
        sections.append(
            MarkdownSection(index=idx, heading=heading, content=content, start_line=start, end_line=end)
        )
    return sections


def join_markdown_sections(sections: list[MarkdownSection]) -> str:
    if not sections:
        return ""
    return "\n".join(s.content for s in sections)


def new_file_line_ranges_from_diff(diff: str) -> list[tuple[int, int]]:
    """Inclusive line ranges in the **new** file touched by a unified diff."""
    ranges: list[tuple[int, int]] = []
    for m in _HUNK_HEADER_RE.finditer(diff):
        new_start = int(m.group(3))
        new_count = int(m.group(4) or 1)
        if new_count <= 0:
            continue
        ranges.append((new_start, new_start + new_count - 1))
    return ranges


def section_indices_touched_by_diff(
    diff: str,
    sections: list[MarkdownSection],
) -> set[int]:
    """Section indices whose line span overlaps any new-file hunk in *diff*."""
    if not diff.strip():
        return set()
    ranges = new_file_line_ranges_from_diff(diff)
    if not ranges:
        return {s.index for s in sections}
    touched: set[int] = set()
    for lo, hi in ranges:
        for sec in sections:
            if sec.end_line >= lo and sec.start_line <= hi:
                touched.add(sec.index)
    return touched


def extract_diff_hunks_for_line_range(
    diff: str,
    *,
    start_line: int,
    end_line: int,
) -> str:
    """
    Return unified-diff hunks whose **new-file** span overlaps ``[start_line, end_line]``.

    Preserves file header lines from *diff* when present.
    """
    if not diff.strip():
        return ""
    lines = diff.splitlines()
    header: list[str] = []
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("@@"):
            body_start = i
            break
        header.append(line)
    if body_start == 0 and lines and not lines[0].startswith("@@"):
        header = []
        body_start = 0

    kept: list[str] = []
    i = body_start
    while i < len(lines):
        line = lines[i]
        if not line.startswith("@@"):
            i += 1
            continue
        m = _HUNK_HEADER_RE.match(line)
        if not m:
            i += 1
            continue
        new_start = int(m.group(3))
        new_count = int(m.group(4) or 1)
        new_end = new_start + max(new_count, 1) - 1
        hunk_lines = [line]
        i += 1
        while i < len(lines) and not lines[i].startswith("@@"):
            hunk_lines.append(lines[i])
            i += 1
        if new_end >= start_line and new_start <= end_line:
            kept.extend(hunk_lines)

    if not kept:
        return ""
    out = list(header)
    if out and kept:
        out.append("")
    out.extend(kept)
    return "\n".join(out)


def align_sections_by_heading(
    source_sections: list[MarkdownSection],
    target_sections: list[MarkdownSection],
) -> list[MarkdownSection | None]:
    """
  Map each *source* section index to a *target* section (or ``None``).

  Match by normalized ``##`` heading text; fall back to same index.
    """
    def norm(h: str) -> str:
        t = h.strip().lower()
        if t.startswith("##"):
            t = t[2:].strip()
        return t

    by_heading: dict[str, MarkdownSection] = {}
    for t in target_sections:
        key = norm(t.heading) if t.heading else f"__preamble_{t.index}"
        by_heading[key] = t

    aligned: list[MarkdownSection | None] = []
    for s in source_sections:
        key = norm(s.heading) if s.heading else f"__preamble_{s.index}"
        hit = by_heading.get(key)
        if hit is None and s.index < len(target_sections):
            hit = target_sections[s.index]
        aligned.append(hit)
    return aligned
