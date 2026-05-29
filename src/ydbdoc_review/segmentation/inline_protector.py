"""Replace protected inline nodes with placeholders for LLM-safe text."""

from __future__ import annotations

from ydbdoc_review.parsing.ast_types import (
    InlineCode,
    InlineEmphasis,
    InlineHardBreak,
    InlineHTML,
    InlineImage,
    InlineLink,
    InlineNode,
    InlineSoftBreak,
    InlineStrong,
    InlineTermRef,
    InlineText,
    InlineVariable,
)
from ydbdoc_review.segmentation.types import ProtectedInline


# Map node kind → placeholder prefix.
_PREFIX_MAP: dict[str, str] = {
    "code": "C",
    "link": "L",
    "image": "I",
    "html_inline": "H",
    "yfm_variable": "V",
    "term_ref": "T",
}


def protect_inline(
    children: list[InlineNode],
) -> tuple[str, list[ProtectedInline]]:
    """Serialise inline children to text, replacing protected atoms with placeholders.

    Returns ``(text, placeholders)``.

    - text: a markdown-rendered string where each protected atom is replaced
      with ⟦K{n}⟧ markers (K = type prefix, n = 1-based index per prefix).
    - placeholders: list of ProtectedInline entries describing what each marker
      stands for. Order in the list matches order of appearance in text.
    """
    out: list[str] = []
    placeholders: list[ProtectedInline] = []
    counters: dict[str, int] = {}

    def take_placeholder(kind: str) -> str:
        prefix = _PREFIX_MAP[kind]
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"⟦{prefix}{counters[prefix]}⟧"

    for node in children:
        kind = node.kind
        if kind in _PREFIX_MAP:
            marker = take_placeholder(kind)
            placeholders.append(ProtectedInline(placeholder=marker, node=node))
            out.append(marker)
            continue

        if isinstance(node, InlineText):
            out.append(node.content)
        elif isinstance(node, InlineEmphasis):
            inner_text, inner_phs = protect_inline(node.children)
            out.append(f"{node.marker}{inner_text}{node.marker}")
            placeholders.extend(inner_phs)
        elif isinstance(node, InlineStrong):
            inner_text, inner_phs = protect_inline(node.children)
            out.append(f"{node.marker}{inner_text}{node.marker}")
            placeholders.extend(inner_phs)
        elif isinstance(node, InlineSoftBreak):
            out.append("\n")
        elif isinstance(node, InlineHardBreak):
            out.append("  \n")
        else:
            # Defensive: anything else passes through as-is via str().
            out.append(str(node))

    # Re-index placeholders so the counters in `text` line up with positional ids.
    # NOTE: counters already gave us correct per-prefix indices; re-indexing not needed.
    return "".join(out), placeholders


def restore_inline_text(text: str, placeholders: list[ProtectedInline]) -> str:
    """Restore the original markdown by replacing placeholders with rendered atoms.

    Each placeholder maps to a single InlineNode; we render that node using the
    same logic as the renderer. To avoid a circular import, the rendering is
    inlined here (small subset of inline rendering).
    """
    from ydbdoc_review.rendering.markdown_renderer import _render_inline_node  # local

    result = text
    for p in placeholders:
        result = result.replace(p.placeholder, _render_inline_node(p.node), 1)
    return result

