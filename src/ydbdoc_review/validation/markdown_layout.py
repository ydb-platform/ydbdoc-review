"""Markdown layout fixes for generated EN docs (markdownlint MD031, MD037)."""

from __future__ import annotations

import re
from collections.abc import Callable

_FENCE_LINE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
# Glossary-style bold links: ``** [text](url)**`` → ``**[text](url)**`` (MD037).
_BOLD_LINK_OPEN = re.compile(r"\*\* \[")
# LLM sometimes emits ``! [alt](src)`` instead of ``![alt](src)``.
_IMAGE_BANG_SPACE = re.compile(r"!(\s+)\[")
_YFM_CONTAINER_LINE = re.compile(
    r"^(\s*)(\{%\s*(?:list\b[^%]*|endlist|cut\b[^%]*|endcut)\s*%\})\s*$"
)
_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s+\S")


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


def fix_no_space_in_emphasis(text: str) -> str:
    """Remove spurious space after ``**`` before a link opener (MD037)."""
    return _BOLD_LINK_OPEN.sub("**[", text)


def fix_image_bang_spacing(text: str) -> str:
    """Normalize ``! [alt](src)`` to ``![alt](src)`` so images parse as images."""
    return _IMAGE_BANG_SPACE.sub("![", text)


def _sync_structural_line_indentation(
    source_lines: list[str],
    target_lines: list[str],
    pattern: re.Pattern[str],
    *,
    token_key: Callable[[str], str] = lambda token: token,
) -> None:
    src = [
        (i, m.group(1), m.group(2))
        for i, line in enumerate(source_lines)
        if (m := pattern.match(line))
    ]
    tgt = [
        (i, m.group(1), m.group(2))
        for i, line in enumerate(target_lines)
        if (m := pattern.match(line))
    ]
    if [token_key(token) for _, _, token in src] != [token_key(token) for _, _, token in tgt]:
        return
    for (_, indent, _), (target_i, _, target_token) in zip(src, tgt, strict=True):
        target_lines[target_i] = indent + target_token


def _drop_renderer_inserted_fence_markers(source_lines: list[str], target_lines: list[str]) -> None:
    """Drop fence markers inserted solely by parse→render normalization.

    Legacy YFM can contain deliberately odd fence indentation. The internal
    renderer balances it by adding closers, but modern Diplodoc then interprets
    surrounding ``endcut``/``endlist`` as unexpected. Only the safe case where
    the raw source marker sequence is a subsequence of target is repaired.
    """
    src = [
        (i, m.group(1), m.group(2) + m.group(3).strip())
        for i, line in enumerate(source_lines)
        if (m := _FENCE_LINE.match(line))
    ]
    tgt = [
        (i, m.group(1), m.group(2) + m.group(3).strip())
        for i, line in enumerate(target_lines)
        if (m := _FENCE_LINE.match(line))
    ]
    src_tokens = [token for _, _, token in src]
    tgt_tokens = [token for _, _, token in tgt]
    if len(tgt_tokens) <= len(src_tokens):
        return
    source_i = 0
    inserted: list[int] = []
    for target_i, token in enumerate(tgt_tokens):
        if source_i < len(src_tokens) and token == src_tokens[source_i]:
            source_i += 1
        else:
            inserted.append(tgt[target_i][0])
    if source_i != len(src_tokens):
        return
    for line_i in reversed(inserted):
        del target_lines[line_i]

    # After deleting renderer-only markers, restore the source indentation.
    remaining = [(i, m) for i, line in enumerate(target_lines) if (m := _FENCE_LINE.match(line))]
    if len(remaining) == len(src):
        for (_, indent, token), (target_i, _) in zip(src, remaining, strict=True):
            target_lines[target_i] = indent + token


def repair_generated_markdown_layout(source_text: str, target_text: str) -> str:
    """Make generated EN preserve legacy YFM structure and lint-safe spacing."""
    had_final_newline = target_text.endswith("\n")
    source_lines = source_text.splitlines()
    target_lines = target_text.splitlines()

    _drop_renderer_inserted_fence_markers(source_lines, target_lines)
    _sync_structural_line_indentation(
        source_lines,
        target_lines,
        _YFM_CONTAINER_LINE,
        token_key=lambda token: token.split(maxsplit=2)[1],
    )

    # The fallback list renderer can emit ``- `` placeholders for structural
    # tab labels that segmentation intentionally excludes. They are empty list
    # items, invalid under MD009, and have no source content (#50741).
    target_lines = [line for line in target_lines if line.strip() not in {"-", "*", "+"}]
    # MD009: renderer prefixes blank lines inside lists with one/four spaces.
    target_lines = ["" if not line.strip() else line for line in target_lines]

    # MD022: ensure a heading is separated from the following block.
    out: list[str] = []
    in_fence = False
    for i, line in enumerate(target_lines):
        out.append(line)
        fence = _FENCE_LINE.match(line)
        if fence:
            in_fence = not in_fence
        if (
            not in_fence
            and _HEADING_LINE.match(line)
            and i + 1 < len(target_lines)
            and target_lines[i + 1].strip()
        ):
            out.append("")

    result = "\n".join(out)
    return result + "\n" if had_final_newline else result
