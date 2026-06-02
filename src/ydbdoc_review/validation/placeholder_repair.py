"""Restore placeholders when the model emits the original atom instead of ⟦X⟧."""

from __future__ import annotations

import re

from ydbdoc_review.parsing.ast_types import (
    InlineCode,
    InlineLink,
    InlineNode,
    InlineVariable,
)
from ydbdoc_review.segmentation.types import ProtectedInline, Segment
from ydbdoc_review.validation.markers import realign_placeholders

# Markdown link destinations that are not already placeholders.
_LINK_DEST_RE = re.compile(r"\]\((?!⟦)([^)]+)\)")

# YFM variables may appear with different whitespace than the stored ``raw``.
_YFM_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")


def _is_url_placeholder_template(node: InlineNode) -> bool:
    return isinstance(node, InlineLink) and not node.children and bool(node.href)


def _repair_missing_url_markers(text: str, url_placeholders: list[ProtectedInline]) -> str:
    """Replace bare ``](url)`` destinations with ⟦U⟧ markers in source order."""
    for protected in url_placeholders:
        marker = protected.placeholder
        if marker in text:
            continue
        text, count = _LINK_DEST_RE.subn(f"]({marker})", text, count=1)
        if not count:
            break
    return text


def _repair_atoms_in_order(segment: Segment, text: str) -> str:
    """Replace rendered atoms left-to-right (handles duplicate ``<br/>``, etc.)."""
    from ydbdoc_review.rendering.markdown_renderer import _render_inline_node

    cursor = 0
    for protected in segment.placeholders:
        marker = protected.placeholder
        pos = text.find(marker, cursor)
        if pos != -1:
            cursor = pos + len(marker)
            continue

        node = protected.node
        if _is_url_placeholder_template(node):
            href = re.escape(node.href)
            match = re.search(rf"\]\({href}\)", text[cursor:])
            if match:
                start = cursor + match.start()
                end = cursor + match.end()
                text = text[: start + 1] + f"({marker})" + text[end:]
                cursor = start + len(f"]({marker})")
                continue

        if isinstance(node, InlineVariable):
            pattern = re.compile(
                r"\{\{\s*" + re.escape(node.name) + r"\s*\}\}"
            )
            match = pattern.search(text, cursor)
            if match:
                start, end = match.span()
                text = text[:start] + marker + text[end:]
                cursor = start + len(marker)
                continue

        rendered = _render_inline_node(node)
        if rendered:
            pos = text.find(rendered, cursor)
            if pos != -1:
                text = text[:pos] + marker + text[pos + len(rendered) :]
                cursor = pos + len(marker)
                continue

        if isinstance(node, InlineCode):
            pos = text.find(node.content, cursor)
            if pos != -1:
                text = text[:pos] + marker + text[pos + len(node.content) :]
                cursor = pos + len(marker)
    return text


def repair_translation_placeholders(segment: Segment, translated: str) -> str:
    """Fix common LLM placeholder mistakes using segment placeholder metadata.

    Runs after ``realign_placeholders`` (renumbered indices) and before strict
    validation. Does not change prose when markers already match.
    """
    text = realign_placeholders(segment.text, translated) or translated
    url_placeholders = [
        p for p in segment.placeholders if _is_url_placeholder_template(p.node)
    ]
    text = _repair_missing_url_markers(text, url_placeholders)
    text = _repair_atoms_in_order(segment, text)
    return text
