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


# Map node kind → placeholder prefix (whole-atom protection).
_PREFIX_MAP: dict[str, str] = {
    "code": "C",
    "image": "I",
    "html_inline": "H",
    "yfm_variable": "V",
    "term_ref": "T",
}
# Links: anchor text is translated; href is protected as ⟦U{n}⟧ (see InlineLink branch).


def protect_inline(
    children: list[InlineNode],
) -> tuple[str, list[ProtectedInline]]:
    """Serialise inline children to text, replacing protected atoms with placeholders.

    Returns ``(text, placeholders)``.

    Placeholder indices are **globally unique within a segment**, including inside
    nested emphasis/strong/link content. This is essential for correct round-trip:
    the same `⟦U{n}⟧` must always refer to exactly one URL template.
    """
    state = _ProtectState()
    text = _protect_walk(children, state)
    return text, state.placeholders


class _ProtectState:
    """Mutable state shared across recursive calls."""

    def __init__(self) -> None:
        self.placeholders: list[ProtectedInline] = []
        self.counters: dict[str, int] = {}

    def next_placeholder(self, kind: str) -> str:
        if kind == "url":
            prefix = "U"
        else:
            prefix = _PREFIX_MAP[kind]
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"⟦{prefix}{self.counters[prefix]}⟧"


def _protect_walk(children: list[InlineNode], state: _ProtectState) -> str:
    out: list[str] = []
    for node in children:
        if isinstance(node, InlineLink):
            inner = _protect_walk(node.children, state)
            marker = state.next_placeholder("url")
            state.placeholders.append(
                ProtectedInline(
                    placeholder=marker,
                    node=InlineLink(
                        href=node.href,
                        title=node.title,
                        children=[],
                    ),
                )
            )
            out.append(f"[{inner}]({marker})")
            continue

        kind = node.kind
        if kind in _PREFIX_MAP:
            marker = state.next_placeholder(kind)
            state.placeholders.append(
                ProtectedInline(placeholder=marker, node=node)
            )
            out.append(marker)
            continue

        if isinstance(node, InlineText):
            out.append(node.content)
        elif isinstance(node, InlineEmphasis):
            inner_text = _protect_walk(node.children, state)
            out.append(f"{node.marker}{inner_text}{node.marker}")
        elif isinstance(node, InlineStrong):
            inner_text = _protect_walk(node.children, state)
            out.append(f"{node.marker}{inner_text}{node.marker}")
        elif isinstance(node, InlineSoftBreak):
            out.append("\n")
        elif isinstance(node, InlineHardBreak):
            out.append("  \n")
        else:
            # Defensive: anything else passes through as-is via str().
            out.append(str(node))

    return "".join(out)


def restore_inline_text(text: str, placeholders: list[ProtectedInline]) -> str:
    """Restore the original markdown by replacing placeholders with rendered atoms.

    Used in tests and for diagnostics; the main pipeline uses
    ``reinsert.py``'s placeholder substitution at the AST level.
    """
    from ydbdoc_review.rendering.markdown_renderer import _render_inline_node

    result = text
    for p in placeholders:
        if (
            isinstance(p.node, InlineLink)
            and not p.node.children
            and p.node.href
        ):
            replacement = p.node.href
        else:
            replacement = _render_inline_node(p.node)
        result = result.replace(p.placeholder, replacement, 1)
    return result
