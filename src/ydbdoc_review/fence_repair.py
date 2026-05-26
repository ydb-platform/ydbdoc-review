"""Deterministic fence repair: restore ``` structure from SOURCE (EN body preserved)."""

from __future__ import annotations

import re

from ydbdoc_review.document_segments import _is_fence_toggle


def extract_fence_blocks(text: str) -> list[str]:
    """Return each fenced block including opening and closing delimiter lines."""
    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        if not _is_fence_toggle(lines[i]):
            i += 1
            continue
        start = i
        i += 1
        while i < len(lines):
            if _is_fence_toggle(lines[i]) and i > start:
                i += 1
                break
            i += 1
        blocks.append("\n".join(lines[start:i]))
    return blocks


def _fence_blocks_balanced(text: str) -> bool:
    n = sum(1 for line in text.splitlines() if _is_fence_toggle(line))
    return n % 2 == 0 and n > 0


def _fence_positions(lines: list[str]) -> list[tuple[int, int]]:
    """Half-open line ranges ``[start, end)`` for each fence region starting with ```."""
    positions: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if not _is_fence_toggle(lines[i]):
            i += 1
            continue
        start = i
        i += 1
        while i < len(lines):
            if _is_fence_toggle(lines[i]) and i > start:
                i += 1
                break
            i += 1
        positions.append((start, i))
    return positions


def _inner_line_count(block: str) -> int:
    lines = block.splitlines()
    if len(lines) < 2:
        return 0
    end = len(lines) - 1 if _is_fence_toggle(lines[-1]) else len(lines)
    return max(0, end - 1)


def _closing_fence_line(block: str) -> str:
    lines = block.splitlines()
    if lines and _is_fence_toggle(lines[-1]):
        return lines[-1]
    return "```"


def repair_fences_from_source(source: str, translation: str) -> tuple[str, bool]:
    """Fix EN fence delimiters using SOURCE; keep translated lines inside blocks.

    Does **not** replace fenced code/config with RU verbatim. Inserts missing closing
    ``` lines and, when needed, closes a block before prose that leaked into the region.
    """
    src_blocks = extract_fence_blocks(source)
    if not src_blocks:
        return translation, False
    if not all(_fence_blocks_balanced(b) for b in src_blocks):
        return translation, False

    lines = translation.splitlines()
    positions = _fence_positions(lines)
    insertions: list[tuple[int, str]] = []
    applied = False

    for idx, (start, end) in enumerate(positions):
        block_lines = lines[start:end]
        if not block_lines:
            continue
        has_closer = len(block_lines) >= 2 and _is_fence_toggle(block_lines[-1])
        if has_closer:
            continue
        src_block = src_blocks[min(idx, len(src_blocks) - 1)]
        closer = _closing_fence_line(src_block)
        src_inner_n = _inner_line_count(src_block)
        insert_at = start + 1 + src_inner_n
        if insert_at > end:
            insert_at = end
        insertions.append((insert_at, closer))
        applied = True

    trn_ticks = sum(1 for line in lines if _is_fence_toggle(line))
    if trn_ticks % 2 == 1 and not applied:
        closer = _closing_fence_line(src_blocks[-1])
        insertions.append((len(lines), closer))
        applied = True

    if not insertions:
        return translation, False

    for insert_at, closer in sorted(insertions, reverse=True):
        lines = lines[:insert_at] + [closer] + lines[insert_at:]

    return "\n".join(lines), True


_FENCE_BLOCKER_RE = re.compile(
    r"fence|fenced|```|маркер|структур",
    re.IGNORECASE,
)


def review_mentions_fence_structure(review_md: str) -> bool:
    return bool(_FENCE_BLOCKER_RE.search(review_md))
