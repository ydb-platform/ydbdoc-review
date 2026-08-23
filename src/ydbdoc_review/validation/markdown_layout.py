"""Markdown layout fixes for generated EN docs (markdownlint MD031, MD037)."""

from __future__ import annotations

import re
from collections.abc import Callable
from difflib import SequenceMatcher

from ydbdoc_review.validation.ru_source_bugs import normalize_legacy_markdown_structure

_FENCE_LINE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
# Glossary-style bold links: ``** [text](url)**`` → ``**[text](url)**`` (MD037).
_BOLD_LINK_OPEN = re.compile(r"\*\* \[")
# LLM sometimes emits ``! [alt](src)`` instead of ``![alt](src)``.
_IMAGE_BANG_SPACE = re.compile(r"!(\s+)\[")
_YFM_CONTAINER_LINE = re.compile(
    r"^(\s*)(\{%\s*(?:list\b[^%]*|endlist|cut\b[^%]*|endcut|if\b[^%]*|endif)\s*%\})\s*$"
)
_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_MARKDOWN_STRUCTURE_LINE = re.compile(r"^\s*(?:[-+*]\s+|\d+[.)]\s+|#{1,6}\s+|>\s*|\|)")
_LIST_ITEM_LINE = re.compile(r"^([ \t]*)([-+*]|\d+[.)])([ \t]+)(.*)$")


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
    if tgt_tokens == src_tokens:
        for (_, indent, _), (target_i, _, target_token) in zip(src, tgt, strict=True):
            target_lines[target_i] = indent + target_token
        return
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


def _sync_unchanged_line_indentation(source_lines: list[str], target_lines: list[str]) -> None:
    """Restore RU indentation for unchanged technical lines."""
    source = [(i, line.strip()) for i, line in enumerate(source_lines) if line.strip()]
    target = [(i, line.strip()) for i, line in enumerate(target_lines) if line.strip()]
    matcher = SequenceMatcher(
        None,
        [text for _, text in source],
        [text for _, text in target],
        autojunk=False,
    )
    for source_start, target_start, size in matcher.get_matching_blocks():
        for offset in range(size):
            source_i, _ = source[source_start + offset]
            target_i, _ = target[target_start + offset]
            if _FENCE_LINE.match(target_lines[target_i]) or _YFM_CONTAINER_LINE.match(
                target_lines[target_i]
            ):
                continue
            if _MARKDOWN_STRUCTURE_LINE.match(
                source_lines[source_i]
            ) or _MARKDOWN_STRUCTURE_LINE.match(target_lines[target_i]):
                continue
            source_indent = source_lines[source_i][
                : len(source_lines[source_i]) - len(source_lines[source_i].lstrip())
            ]
            target_lines[target_i] = source_indent + target_lines[target_i].lstrip()


def _sync_stable_fence_body_indentation(
    source_text: str, source_lines: list[str], target_lines: list[str]
) -> None:
    """Preserve source indentation when comments inside code are translated."""
    from ydbdoc_review.validation.fence_integrity import (
        fence_structure_is_round_trip_stable,
    )

    if not fence_structure_is_round_trip_stable(source_text):
        return
    source_markers = [i for i, line in enumerate(source_lines) if _FENCE_LINE.match(line)]
    target_markers = [i for i, line in enumerate(target_lines) if _FENCE_LINE.match(line)]
    if len(source_markers) != len(target_markers) or len(source_markers) % 2:
        return
    for marker_i in range(0, len(source_markers), 2):
        source_start, source_end = source_markers[marker_i : marker_i + 2]
        target_start, target_end = target_markers[marker_i : marker_i + 2]
        source_body = source_lines[source_start + 1 : source_end]
        target_body = target_lines[target_start + 1 : target_end]
        if len(source_body) != len(target_body):
            continue
        for offset, (source_line, target_line) in enumerate(
            zip(source_body, target_body, strict=True), start=1
        ):
            if not target_line.strip():
                continue
            indent = source_line[: len(source_line) - len(source_line.lstrip())]
            target_lines[target_start + offset] = indent + target_line.lstrip()


def _normalize_target_list_indentation(target_lines: list[str]) -> None:
    """Use the target AST itself to normalize list/tab label indentation."""
    from ydbdoc_review.parsing.markdown_parser import parse_markdown
    from ydbdoc_review.rendering.markdown_renderer import render_markdown

    rendered_lines = render_markdown(
        parse_markdown("\n".join(target_lines)), target_lang="en"
    ).splitlines()
    target_items = [
        (i, m.group(2), m.group(3), m.group(4))
        for i, line in enumerate(target_lines)
        if (m := _LIST_ITEM_LINE.match(line))
    ]
    rendered_items = [
        (m.group(1), m.group(2), m.group(3), m.group(4))
        for line in rendered_lines
        if (m := _LIST_ITEM_LINE.match(line))
    ]
    matcher = SequenceMatcher(
        None,
        [(marker, text) for _, marker, _, text in target_items],
        [(marker, text) for _, marker, _, text in rendered_items],
        autojunk=False,
    )
    for target_start, rendered_start, size in matcher.get_matching_blocks():
        for offset in range(size):
            target_i, marker, spacing, text = target_items[target_start + offset]
            indent, _, _, _ = rendered_items[rendered_start + offset]
            target_lines[target_i] = indent + marker + spacing + text


def repair_generated_markdown_layout(source_text: str, target_text: str) -> str:
    """Make generated EN preserve legacy YFM structure and lint-safe spacing."""
    source_text = normalize_legacy_markdown_structure(source_text)
    had_final_newline = target_text.endswith("\n")
    source_lines = source_text.splitlines()
    target_lines = target_text.splitlines()

    _drop_renderer_inserted_fence_markers(source_lines, target_lines)
    target_text = normalize_legacy_markdown_structure(
        "\n".join(target_lines) + ("\n" if had_final_newline else "")
    )
    target_lines = target_text.splitlines()
    _sync_structural_line_indentation(
        source_lines,
        target_lines,
        _YFM_CONTAINER_LINE,
        token_key=lambda token: token.split(maxsplit=2)[1],
    )
    _normalize_target_list_indentation(target_lines)
    _sync_unchanged_line_indentation(source_lines, target_lines)
    _sync_stable_fence_body_indentation(source_text, source_lines, target_lines)

    # The fallback list renderer can emit ``- `` placeholders for structural
    # tab labels that segmentation intentionally excludes. They are empty list
    # items, invalid under MD009, and have no source content (#50741).
    target_lines = [line for line in target_lines if line.strip() not in {"-", "*", "+"}]
    # MD009: keep only intentional two-space Markdown hard breaks.
    target_lines = [
        ""
        if not line.strip()
        else line
        if line.endswith("  ") and not line.endswith("   ")
        else line.rstrip()
        for line in target_lines
    ]

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
