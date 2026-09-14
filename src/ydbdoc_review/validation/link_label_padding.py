"""Conservative repair for model-added padding in Markdown link labels."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ydbdoc_review.parsing.inline_locations import LocatedInlineLink
from ydbdoc_review.parsing.markdown_parser import parse_markdown_located

_HORIZONTAL_PADDING = " \t"


@dataclass(frozen=True)
class _InlineLinkBounds:
    link: LocatedInlineLink
    label_start: int
    label_end: int


def _matching_label_end(text: str, start: int, end: int) -> int | None:
    """Find the parser-proven inline link's closing label bracket."""
    if start >= end or text[start] != "[":
        return None
    depth = 1
    index = start + 1
    while index < end:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "`":
            run_end = index + 1
            while run_end < end and text[run_end] == "`":
                run_end += 1
            marker = text[index:run_end]
            closing = text.find(marker, run_end, end)
            if closing < 0:
                return None
            index = closing + len(marker)
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index if index + 1 < end and text[index + 1] == "(" else None
        index += 1
    return None


def _ordinary_inline_links(text: str) -> list[_InlineLinkBounds] | None:
    try:
        located = parse_markdown_located(text)
    except Exception:  # Malformed or unsupported Markdown must stay untouched.
        return None

    links: list[_InlineLinkBounds] = []
    for segment in located.inline_segments:
        for link in segment.links:
            span = link.span
            if span is None:
                continue
            label_end = _matching_label_end(text, span.start, span.end)
            if label_end is None:
                # Images, autolinks, and reference-style links are intentionally
                # outside this repair even when the AST exposes them as links.
                continue
            links.append(
                _InlineLinkBounds(
                    link=link,
                    label_start=span.start + 1,
                    label_end=label_end,
                )
            )
    return links


def _horizontal_boundary_replacements(
    text: str,
    bounds: _InlineLinkBounds,
) -> list[tuple[int, int]]:
    """Return safe deletion spans, or none for multiline/empty labels."""
    if "\n" in text[bounds.label_start : bounds.label_end] or "\r" in text[
        bounds.label_start : bounds.label_end
    ]:
        return []
    left = bounds.label_start
    right = bounds.label_end
    while left < right and text[left] in _HORIZONTAL_PADDING:
        left += 1
    while right > left and text[right - 1] in _HORIZONTAL_PADDING:
        right -= 1
    if left == right:
        return []

    replacements: list[tuple[int, int]] = []
    if left > bounds.label_start:
        replacements.append((bounds.label_start, left))
    if right < bounds.label_end:
        replacements.append((right, bounds.label_end))
    return replacements


def repair_markdown_link_label_padding(source_text: str, target_text: str) -> str:
    """Remove only model-added horizontal padding from ordinary link labels.

    The source and target must expose the same number of parser-proven inline
    links. Ambiguous, malformed, multiline, image, reference, autolink, and
    code-fence cases stay byte-identical so the existing heuristic remains a
    non-green signal when a safe repair cannot be proved.
    """
    source_links = _ordinary_inline_links(source_text)
    target_links = _ordinary_inline_links(target_text)
    if source_links is None or target_links is None or len(source_links) != len(target_links):
        return target_text

    source_by_identity: defaultdict[tuple[str, str | None], list[_InlineLinkBounds]] = (
        defaultdict(list)
    )
    target_by_identity: defaultdict[tuple[str, str | None], list[_InlineLinkBounds]] = (
        defaultdict(list)
    )
    for item in source_links:
        source_by_identity[(item.link.node.href, item.link.node.title)].append(item)
    for item in target_links:
        target_by_identity[(item.link.node.href, item.link.node.title)].append(item)

    replacements: list[tuple[int, int]] = []
    for identity, target_matches in target_by_identity.items():
        source_matches = source_by_identity.get(identity, [])
        if len(source_matches) != 1 or len(target_matches) != 1:
            continue
        source = source_matches[0]
        target = target_matches[0]
        if source.link.leading_padding or source.link.trailing_padding:
            continue
        if not (target.link.leading_padding or target.link.trailing_padding):
            continue
        candidate = _horizontal_boundary_replacements(target_text, target)
        if not candidate:
            continue
        replacements.extend(candidate)

    if not replacements:
        return target_text

    repaired = target_text
    for start, end in sorted(replacements, reverse=True):
        repaired = repaired[:start] + repaired[end:]

    # Re-parse before accepting the edit and prove that link destinations and
    # titles are unchanged. All other bytes are preserved by the deletion spans.
    repaired_links = _ordinary_inline_links(repaired)
    if repaired_links is None or len(repaired_links) != len(target_links):
        return target_text
    before_contract = [(item.link.node.href, item.link.node.title) for item in target_links]
    after_contract = [(item.link.node.href, item.link.node.title) for item in repaired_links]
    if before_contract != after_contract:
        return target_text
    return repaired
