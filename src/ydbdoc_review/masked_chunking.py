"""Safe split points for masked translation chunks (tables, blocks, placeholders)."""

from __future__ import annotations

import re

from ydbdoc_review.document_mask import PLACEHOLDER_RE
from ydbdoc_review.document_segments import _is_fence_toggle

# Cut *before* these line starts (chunk ends at the preceding newline).
_LINE_BOUNDARY_RE = re.compile(
    r"\n(?="
    r"\|"  # markdown table row
    r"|#{1,6}\s"  # heading
    r"|\{%"  # diplodoc directive
    r"|```"  # fenced block
    r"|- [^\n]"  # list / tab label at column 0
    r")"
)


def _inside_placeholder(text: str, index: int) -> bool:
    for m in PLACEHOLDER_RE.finditer(text):
        if m.start() < index < m.end():
            return True
    return False


def _extend_cut_past_open_fence(text: str, cut: int) -> int:
    """If *cut* falls inside an unclosed fenced block, move to after its closing line."""
    ticks = sum(1 for line in text[:cut].splitlines() if _is_fence_toggle(line))
    if ticks % 2 == 0:
        return cut
    pos = text.find("\n", cut)
    pos = cut if pos == -1 else pos + 1
    while pos < len(text):
        nxt = text.find("\n", pos)
        if nxt == -1:
            line = text[pos:]
            nxt = len(text)
        else:
            line = text[pos:nxt]
        if _is_fence_toggle(line):
            return nxt + 1 if nxt < len(text) else len(text)
        pos = nxt + 1
    return len(text)


def _clamp_cut(text: str, start: int, cut: int) -> int:
    """Move *cut* left if it would split a ``⟦…⟧`` token."""
    cut = min(max(cut, start + 1), len(text))
    while cut > start and _inside_placeholder(text, cut):
        cut -= 1
    return cut


def find_chunk_end(text: str, start: int, limit: int) -> int:
    """
    Return exclusive end index for the next chunk starting at *start*.

    Prefers, in order: paragraph breaks, table rows, headings, diplodoc/fences,
    list lines; never splits inside ``⟦…⟧``.
    """
    hard_end = min(start + limit, len(text))
    if hard_end >= len(text):
        return len(text)

    segment = text[start:hard_end]
    best = start

    for m in re.finditer(r"\n\n+", segment):
        pos = start + m.end()
        if pos > start:
            best = max(best, pos)

    for m in _LINE_BOUNDARY_RE.finditer(segment):
        pos = start + m.start() + 1
        if pos > start:
            best = max(best, _clamp_cut(text, start, pos))

    if best > start:
        return best

    for m in re.finditer(r"\n", segment):
        pos = start + m.end()
        if pos > start:
            best = max(best, _clamp_cut(text, start, pos))

    if best > start:
        return best

    # Last resort: hard limit, but never mid-table-row or inside a fence.
    end = _clamp_cut(text, start, hard_end)
    end = _extend_cut_past_open_fence(text, end)
    line_start = text.rfind("\n", start, end) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    if line.lstrip().startswith("|") and end < line_end:
        return line_end
    space = text.rfind(" ", start, end)
    if space > start:
        return space + 1
    return end


def chunk_masked_text(text: str, *, max_chars: int) -> list[str]:
    """Split *text* into chunks no larger than *max_chars* at safe boundaries."""
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = find_chunk_end(text, start, max_chars)
        if end <= start:
            end = min(start + max_chars, n)
            end = _clamp_cut(text, start, end)
        chunks.append(text[start:end])
        start = end
    return [c for c in chunks if c]
