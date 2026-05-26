"""Translate only comments inside fenced code/config blocks; keep code verbatim."""

from __future__ import annotations

import re
from typing import Callable

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0450-\u045F]")
_INLINE_SQL_COMMENT_RE = re.compile(r"(--)([^\"']*)$")
_INLINE_HASH_COMMENT_RE = re.compile(r"(#)([^\"'\n]*)$")


def _is_fence_delimiter(line: str) -> bool:
    return line.strip().startswith("```")


def comment_body_on_line(line: str) -> tuple[str, str] | None:
    """
    If *line* is a translatable comment line, return ``(marker, body)``.

    *marker* is ``--`` or ``#``; *body* is text to send to the translator.
    """
    if _is_fence_delimiter(line):
        return None
    stripped = line.lstrip()
    if not stripped:
        return None
    if stripped.startswith("--"):
        body = stripped[2:].strip()
        return ("--", body) if body else None
    if stripped.startswith("#") and not stripped.startswith("#!"):
        body = stripped[1:].strip()
        return ("#", body) if body else None
    return None


def inline_sql_comment_tail(line: str) -> tuple[str, str] | None:
    """Trailing ``-- comment`` on a code line: ``(prefix_before_dashes, comment_body)``."""
    m = _INLINE_SQL_COMMENT_RE.search(line)
    if not m:
        return None
    body = m.group(2).strip()
    if not body:
        return None
    return line[: m.start(1)], body


def inline_hash_comment_tail(line: str) -> tuple[str, str] | None:
    """Trailing ``# comment`` on a code/YAML line: ``(prefix_before_hash, comment_body)``."""
    stripped = line.lstrip()
    if stripped.startswith("#") and not stripped.startswith("#!"):
        return None
    m = _INLINE_HASH_COMMENT_RE.search(line)
    if not m:
        return None
    body = m.group(2).strip()
    if not body:
        return None
    return line[: m.start(1)], body


def translate_fence_comments(
    fence_text: str,
    translate_comment: Callable[[str], str],
    *,
    only_if_cyrillic: bool = False,
) -> str:
    """
    Walk a single fenced block; translate comment lines only, copy code lines unchanged.
    """
    lines = fence_text.split("\n")
    out: list[str] = []
    for line in lines:
        if _is_fence_delimiter(line):
            out.append(line)
            continue
        full = comment_body_on_line(line)
        if full is not None:
            marker, body = full
            indent = line[: len(line) - len(line.lstrip())]
            if only_if_cyrillic and not _CYRILLIC_RE.search(body):
                out.append(line)
                continue
            translated = translate_comment(body).strip()
            out.append(f"{indent}{marker} {translated}")
            continue
        inline = inline_sql_comment_tail(line)
        if inline is not None:
            prefix, body = inline
            if only_if_cyrillic and not _CYRILLIC_RE.search(body):
                out.append(line)
                continue
            translated = translate_comment(body).strip()
            out.append(f"{prefix}-- {translated}")
            continue
        inline_hash = inline_hash_comment_tail(line)
        if inline_hash is not None:
            prefix, body = inline_hash
            if only_if_cyrillic and not _CYRILLIC_RE.search(body):
                out.append(line)
                continue
            translated = translate_comment(body).strip()
            out.append(f"{prefix}# {translated}")
            continue
        out.append(line)
    return "\n".join(out)


