# ruff: noqa: RUF001
"""Read-only QA for Cyrillic in fenced code comments."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ydbdoc_review.parsing.ast_types import FencedCode
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.validation.fence_integrity import collect_code_blocks

logger = logging.getLogger(__name__)

_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")
_FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")
# Line comments in ydb docs: Go/C++/C#/Java ``//``, Python/shell ``#``, YQL/SQL ``--``.
_COMMENT_LINE = re.compile(
    r"^(?P<indent>\s*)(?P<marker>//|#)(?P<spacing>\s*)(?P<body>.*)$"
)
_SQL_LINE_COMMENT = re.compile(
    r"^(?P<indent>\s*)--(?P<spacing>\s+)(?P<body>.*)$"
)
_SQL_TRAILING_COMMENT = re.compile(r"(?P<prefix>.*?)(?P<marker>\s--\s+)(?P<body>[^\n]*)$")
# Trailing ``//`` on a code line (``panic(err) // comment``), not ``://`` in URLs.
_SLASH_TRAILING_COMMENT = re.compile(r"(?P<prefix>.*?)(?P<marker>\s//\s*)(?P<body>[^\n]*)$")
# Trailing YAML/shell ``#`` (``disk_scope: <x>  # optional``). Prefer after ``//`` / ``--``.
_HASH_TRAILING_COMMENT = re.compile(r"(?P<prefix>.*?)(?P<marker>\s+#\s*)(?P<body>[^\n]*)$")


@dataclass(frozen=True)
class FenceCommentLine:
    block_index: int
    line_index: int
    line: str
    body: str


def _trailing_comment_match(line: str) -> re.Match[str] | None:
    for trail_re in (
        _SQL_TRAILING_COMMENT,
        _SLASH_TRAILING_COMMENT,
        _HASH_TRAILING_COMMENT,
    ):
        m = trail_re.match(line)
        if m is not None:
            return m
    return None


def trailing_comment_code_prefix(line: str) -> str | None:
    """Source code before a trailing ``//`` / ``--`` / ``#`` comment; ``None`` otherwise."""
    m = _trailing_comment_match(line)
    return m.group("prefix") if m is not None else None


def _comment_body_if_cyrillic(line: str) -> str | None:
    for matcher in (_COMMENT_LINE.match, _SQL_LINE_COMMENT.match):
        m = matcher(line)
        if m is None:
            continue
        body = m.group("body")
        if body.strip() and _CYRILLIC.search(body):
            return body
    trail = _trailing_comment_match(line)
    if trail is not None:
        body = trail.group("body")
        if body.strip() and _CYRILLIC.search(body):
            return body
    return None


def _replace_comment_body(line: str, new_body: str, *, old_body: str | None = None) -> str:
    m = _COMMENT_LINE.match(line)
    if m:
        spacing = m.group("spacing") or " "
        return (
            f"{m.group('indent')}{m.group('marker')}{spacing}{new_body.lstrip()}"
        )
    m = _SQL_LINE_COMMENT.match(line)
    if m:
        spacing = m.group("spacing") or " "
        return f"{m.group('indent')}--{spacing}{new_body.lstrip()}"
    trail = _trailing_comment_match(line)
    if trail is not None:
        body = trail.group("body")
        if old_body is not None and body.strip() != old_body.strip():
            return line
        return f"{trail.group('prefix')}{trail.group('marker')}{new_body.lstrip()}"
    return line


def collect_cyrillic_fence_comment_lines(text: str) -> list[FenceCommentLine]:
    """Ordered ``//`` / ``#`` / ``--`` comment lines with Cyrillic inside fenced blocks."""
    blocks = collect_code_blocks(parse_markdown(text))
    found: list[FenceCommentLine] = []
    for block_index, block in enumerate(blocks, start=1):
        for line_index, line in enumerate(block.content.splitlines()):
            body = _comment_body_if_cyrillic(line)
            if body is not None:
                found.append(
                    FenceCommentLine(
                        block_index=block_index,
                        line_index=line_index,
                        line=line,
                        body=body,
                    )
                )
    return found


def _iter_fence_comment_lines_in_text(text: str):
    """Yield (block_no, line_no, line) for Cyrillic comment lines in raw markdown."""
    lines = text.splitlines()
    in_fence = False
    fence_char = ""
    block_no = 0
    line_no = 0
    for line in lines:
        m = _FENCE_OPEN.match(line)
        if m:
            marker = m.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                block_no += 1
                line_no = 0
            elif marker[0] == fence_char:
                in_fence = False
            continue
        if in_fence:
            line_no += 1
            if _comment_body_if_cyrillic(line) is not None:
                yield block_no, line_no, line


def check_cyrillic_in_en_fence_comments(
    target_text: str,
    *,
    target_lang: str,
) -> list[str]:
    """Warn when EN fenced ``//`` / ``#`` / ``--`` comments still contain Cyrillic."""
    if target_lang.lower() != "en":
        return []
    all_items = list(_iter_fence_comment_lines_in_text(target_text))
    if not all_items:
        return []
    warnings: list[str] = []
    seen: set[str] = set()
    for block_no, line_no, line in all_items[:8]:
        body = _comment_body_if_cyrillic(line) or ""
        snippet = body.strip().replace("\n", " ")[:80]
        if snippet in seen:
            continue
        seen.add(snippet)
        warnings.append(
            "cyrillic_in_fence: "
            f"block {block_no} line {line_no}: «{snippet}»"
        )
    if len(all_items) > 8:
        warnings.append(
            "cyrillic_in_fence: "
            f"… и ещё {len(all_items) - 8} строк с кириллицей в комментариях"
        )
    return warnings


def _text_fence_lang(info: str) -> str:
    parts = (info or "").strip().split()
    return parts[0].lower() if parts else ""


def _diagram_fence_lang(info: str) -> str | None:
    """Fence languages whose inline labels are translated (``text``, ``mermaid``)."""
    lang = _text_fence_lang(info)
    if lang in {"text", "mermaid"}:
        return lang
    return None


def _preserve_leading_indent(original_line: str, new_content: str) -> str:
    m = re.match(r"^(\s*)", original_line)
    prefix = m.group(1) if m else ""
    return prefix + new_content.lstrip()


def collect_cyrillic_text_fence_lines(text: str) -> list[FenceCommentLine]:
    """Cyrillic lines inside `` ```text `` / `` ```mermaid `` diagram blocks."""
    blocks = collect_code_blocks(parse_markdown(text))
    found: list[FenceCommentLine] = []
    for block_index, block in enumerate(blocks, start=1):
        if not isinstance(block, FencedCode):
            continue
        if _diagram_fence_lang(block.info) is None:
            continue
        for line_index, line in enumerate(block.content.splitlines()):
            if _CYRILLIC.search(line):
                found.append(
                    FenceCommentLine(
                        block_index=block_index,
                        line_index=line_index,
                        line=line,
                        body=line.strip(),
                    )
                )
    return found


def check_cyrillic_in_en_text_fences(target_text: str, *, target_lang: str) -> list[str]:
    """Residual Cyrillic inside `` ```text `` diagram fences."""
    if target_lang.lower() != "en":
        return []
    items = collect_cyrillic_text_fence_lines(target_text)
    if not items:
        return []
    warnings: list[str] = []
    for item in items[:8]:
        preview = item.body.replace("\n", " ")[:120]
        warnings.append(f"cyrillic_in_text_fence: «{preview}»")
    if len(items) > 8:
        warnings.append(f"… and {len(items) - 8} more cyrillic_in_text_fence lines")
    return warnings
