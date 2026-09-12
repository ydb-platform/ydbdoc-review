"""Extract RU/EN context snippets for heuristic warnings in PR reports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

from ydbdoc_review.reporting.locations import (
    ReportLinkContext,
    format_line_ref,
    line_range_for_needle,
)
from ydbdoc_review.validation.link_locale import _WIKI_HREF_IN_TEXT
from ydbdoc_review.validation.wikipedia_links import parse_wikipedia_href

_LINK_LOCALE_WIKI = re.compile(
    r"^link_locale: en\.wikipedia\.org uses Russian article slug "
    r"\(use English title\): (https?://\S+)$"
)
_LINK_LOCALE_RU_HOST = re.compile(r"^link_locale: RU-locale URL in EN document: (\S+)$")
_LINK_LOCALE_CYRILLIC = re.compile(
    r"^link_locale: Cyrillic path on EN-locale URL: (\S+)$"
)

_TRUNC = 200


def _truncate(text: str, *, max_len: int = _TRUNC) -> str:
    one = re.sub(r"\s+", " ", text).strip()
    if len(one) <= max_len:
        return one
    return one[: max_len - 1] + "…"


def _line_excerpt(text: str, line_no: int) -> str | None:
    lines = text.splitlines()
    if not (1 <= line_no <= len(lines)):
        return None
    return _truncate(lines[line_no - 1])


def _href_from_heuristic(message: str) -> str | None:
    for pattern in (_LINK_LOCALE_WIKI, _LINK_LOCALE_RU_HOST, _LINK_LOCALE_CYRILLIC):
        m = pattern.match(message.strip())
        if m:
            return m.group(1)
    if message.startswith("link_locale:"):
        tail = message.split(":", 1)[-1].strip()
        if tail.startswith("http"):
            return tail.split()[0]
    return None


def _find_source_excerpt_for_wiki(
    href: str,
    segment_source_excerpts: dict[str, str],
) -> str | None:
    parsed = parse_wikipedia_href(href)
    title = parsed[1] if parsed else ""
    title_prefix = title[:24] if title else ""
    for excerpt in segment_source_excerpts.values():
        low = excerpt.lower()
        if "wikipedia.org" in low:
            return _truncate(excerpt)
        if title_prefix and title_prefix in excerpt:
            return _truncate(excerpt)
    return None


@dataclass(frozen=True)
class HeuristicContext:
    source_excerpt: str | None = None
    target_excerpt: str | None = None
    line_range: tuple[int, int] | None = None


def heuristic_context_for_message(
    message: str,
    *,
    target_text: str,
    segment_source_excerpts: dict[str, str],
) -> HeuristicContext:
    """Locate RU/EN snippets and line numbers for a heuristic warning."""
    href = _href_from_heuristic(message)
    if href is None:
        return HeuristicContext()

    line_range = line_range_for_needle(target_text, href)
    target_excerpt = None
    if line_range:
        target_excerpt = _line_excerpt(target_text, line_range[0])
    if target_excerpt is None:
        for match in _WIKI_HREF_IN_TEXT.finditer(target_text):
            if unquote(match.group(0)) == unquote(href) or match.group(0) == href:
                start = target_text.count("\n", 0, match.start()) + 1
                target_excerpt = _line_excerpt(target_text, start)
                line_range = line_range or (start, start)
                break

    source_excerpt = _find_source_excerpt_for_wiki(href, segment_source_excerpts)
    return HeuristicContext(
        source_excerpt=source_excerpt,
        target_excerpt=target_excerpt,
        line_range=line_range,
    )


def format_heuristic_location(
    message: str,
    *,
    file_path: str,
    link: ReportLinkContext | None,
    line_range: tuple[int, int] | None,
    default_label: str,
) -> str:
    """Location column with optional deep link to the offending line."""
    if line_range:
        line_suffix = format_line_ref(line_range, file_path=file_path, link=link)
        if line_suffix:
            return f"{default_label}{line_suffix}"
        start, end = line_range
        if start == end:
            return f"{default_label} (строка {start})"
        return f"{default_label} (строки {start}–{end})"
    return default_label
