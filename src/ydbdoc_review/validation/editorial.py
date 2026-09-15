"""Narrow deterministic editorial checks for visible English Markdown prose."""

from __future__ import annotations

import re
from functools import lru_cache

from ydbdoc_review.parsing.inline_locations import exact_source_offset
from ydbdoc_review.parsing.markdown_parser import parse_markdown_located

LDAP_SCHEME_TYPO = re.compile(r"\bldaps\s+schema\b", re.IGNORECASE)
_NEWLINE = re.compile(r"\r\n?|\n")


def _source_line(text: str, offset: int) -> tuple[int, str]:
    """Return one CommonMark physical line and its one-based number."""
    line_number = 1
    line_start = 0
    for newline in _NEWLINE.finditer(text):
        if offset <= newline.start():
            return line_number, text[line_start : newline.start()].strip(" \t")
        line_number += 1
        line_start = newline.end()
    return line_number, text[line_start:].strip(" \t")


def _message(code: str, *, offset: int, text: str) -> str:
    line_number, context = _source_line(text, offset)
    return f"{code}: line {line_number}: «{context}»"


def check_en_editorial(text: str, *, target_lang: str = "en") -> list[str]:
    if target_lang.lower() not in {"en", "english"}:
        return []
    return list(_check_en_editorial_cached(text))


@lru_cache(maxsize=32)
def _check_en_editorial_cached(text: str) -> tuple[str, ...]:
    """Flag two exact incident forms in visible English Markdown content.

    This is intentionally not a general grammar or style checker. It never
    rewrites the document. Findings without an exact parser-owned source
    location are skipped rather than being attributed to the start of a block.
    """
    located = parse_markdown_located(text)
    messages: list[str] = []

    for segment in located.inline_segments:
        for link in segment.links:
            label = link.label
            if not label.text or not (
                link.leading_padding or link.trailing_padding
            ):
                continue
            trailing = not link.leading_padding
            label_index = len(label.text) - 1 if trailing else 0
            offset = exact_source_offset(label, label_index, trailing=trailing)
            if offset is None:
                continue
            messages.append(
                _message(
                    "editorial_link_label_space",
                    offset=offset,
                    text=text,
                )
            )

        ldap_match = LDAP_SCHEME_TYPO.search(segment.visible.text)
        if ldap_match is None:
            continue
        offset = exact_source_offset(segment.visible, ldap_match.start())
        if offset is None:
            continue
        messages.append(
            _message(
                "editorial_ldap_scheme",
                offset=offset,
                text=text,
            )
        )

    return tuple(messages)
