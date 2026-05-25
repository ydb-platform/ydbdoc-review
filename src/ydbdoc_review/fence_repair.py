"""Deterministic fence repair: restore ``` structure from SOURCE."""

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


def repair_fences_from_source(source: str, translation: str) -> tuple[str, bool]:
    """Replace EN fence blocks with SOURCE blocks (code verbatim, structure fixed).

    Returns ``(new_translation, applied)``. Only runs when block counts match and
    SOURCE fences are balanced.
    """
    src_blocks = extract_fence_blocks(source)
    if not src_blocks:
        return translation, False
    if not all(_fence_blocks_balanced(b) for b in src_blocks):
        return translation, False

    trn_blocks = extract_fence_blocks(translation)
    if len(trn_blocks) != len(src_blocks):
        return translation, False

    out_lines = translation.splitlines()
    trn_positions: list[tuple[int, int]] = []
    i = 0
    while i < len(out_lines):
        if not _is_fence_toggle(out_lines[i]):
            i += 1
            continue
        start = i
        i += 1
        while i < len(out_lines):
            if _is_fence_toggle(out_lines[i]) and i > start:
                i += 1
                break
            i += 1
        trn_positions.append((start, i))

    if len(trn_positions) != len(src_blocks):
        return translation, False

    new_lines = list(out_lines)
    for (start, end), src_block in zip(
        reversed(trn_positions), reversed(src_blocks), strict=True
    ):
        src_lines = src_block.splitlines()
        new_lines[start:end] = src_lines

    return "\n".join(new_lines), True


_FENCE_BLOCKER_RE = re.compile(
    r"fence|fenced|```|маркер|структур",
    re.IGNORECASE,
)


def review_mentions_fence_structure(review_md: str) -> bool:
    return bool(_FENCE_BLOCKER_RE.search(review_md))
