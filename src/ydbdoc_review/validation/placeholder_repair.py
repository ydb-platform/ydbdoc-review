"""Restore placeholders when the model emits the original atom instead of ⟦X⟧."""

from __future__ import annotations

import re

from ydbdoc_review.parsing.ast_types import InlineCode, InlineLink, InlineNode
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.validation.markers import realign_placeholders


def _is_url_placeholder_template(node: InlineNode) -> bool:
    return isinstance(node, InlineLink) and not node.children and bool(node.href)


def repair_translation_placeholders(segment: Segment, translated: str) -> str:
    """Fix common LLM placeholder mistakes using segment placeholder metadata.

    Runs after ``realign_placeholders`` (renumbered indices) and before strict
    validation. Does not change prose when markers already match.
    """
    text = realign_placeholders(segment.text, translated) or translated
    from ydbdoc_review.rendering.markdown_renderer import _render_inline_node

    for protected in segment.placeholders:
        marker = protected.placeholder
        if marker in text:
            continue
        node = protected.node
        if _is_url_placeholder_template(node):
            href = re.escape(node.href)
            text, count = re.subn(
                rf"\]\({href}\)",
                f"]({marker})",
                text,
                count=1,
            )
            if count:
                continue
        rendered = _render_inline_node(node)
        if rendered and rendered in text:
            text = text.replace(rendered, marker, 1)
            continue
        if isinstance(node, InlineCode) and node.content in text:
            text = text.replace(node.content, marker, 1)
    return text
