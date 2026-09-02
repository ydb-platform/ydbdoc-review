"""Replace protected inline nodes with placeholders for LLM-safe text."""

from __future__ import annotations

from ydbdoc_review.parsing.ast_types import (
    InlineEmphasis,
    InlineHardBreak,
    InlineHTML,
    InlineImage,
    InlineLink,
    InlineNode,
    InlineSoftBreak,
    InlineStrike,
    InlineStrong,
    InlineText,
)
from ydbdoc_review.segmentation.types import ProtectedInline


# List/table HTML scaffolding — leave literal so the model does not drop ⟦H⟧ markers.
_BORING_HTML: frozenset[str] = frozenset(
    {"<br/>", "<br>", "<ul>", "</ul>", "<li>", "</li>"}
)

# Map node kind → placeholder prefix (whole-atom protection).
_PREFIX_MAP: dict[str, str] = {
    "code": "C",
    "html_inline": "H",
    "yfm_variable": "V",
    "term_ref": "T",
}
# Containers retain their own parser-owned wrapper atoms. Only their nested
# prose is exposed to a model.


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
        elif kind == "src":
            prefix = "S"
        else:
            prefix = _PREFIX_MAP[kind]
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"⟦{prefix}{self.counters[prefix]}⟧"

    def next_boundaries(self, prefix: str) -> tuple[str, str]:
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        identity = self.counters[prefix]
        return f"⟦{prefix}BEGIN_{identity}⟧", f"⟦{prefix}END_{identity}⟧"


def _protect_walk(children: list[InlineNode], state: _ProtectState) -> str:
    out: list[str] = []
    for node in children:
        if isinstance(node, InlineLink):
            inner = _protect_walk(node.children, state)
            begin, end = state.next_boundaries("L")
            state.placeholders.append(
                ProtectedInline(
                    placeholder=begin,
                    node=InlineLink(
                        href=node.href,
                        title=node.title,
                        children=[],
                    ),
                )
            )
            state.placeholders.append(
                ProtectedInline(
                    placeholder=end,
                    node=InlineLink(
                        href=node.href,
                        title=node.title,
                        children=[],
                    ),
                )
            )
            out.append(f"{begin}{inner}{end}")
            continue

        if isinstance(node, InlineImage):
            begin, end = state.next_boundaries("IMG")
            state.placeholders.append(
                ProtectedInline(
                    placeholder=begin,
                    node=InlineImage(
                        src=node.src,
                        title=node.title,
                        alt="",
                        width=node.width,
                        height=node.height,
                    ),
                )
            )
            state.placeholders.append(
                ProtectedInline(
                    placeholder=end,
                    node=InlineImage(
                        src=node.src,
                        title=node.title,
                        alt="",
                        width=node.width,
                        height=node.height,
                    ),
                )
            )
            out.append(f"{begin}{node.alt}{end}")
            continue

        kind = node.kind
        if isinstance(node, InlineHTML) and node.content in _BORING_HTML:
            out.append(node.content)
            continue
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
            begin, end = state.next_boundaries("EM")
            template = InlineEmphasis(children=[], marker=node.marker)
            state.placeholders.extend((
                ProtectedInline(placeholder=begin, node=template),
                ProtectedInline(placeholder=end, node=template),
            ))
            out.append(f"{begin}{inner_text}{end}")
        elif isinstance(node, InlineStrong):
            inner_text = _protect_walk(node.children, state)
            begin, end = state.next_boundaries("STRONG")
            template = InlineStrong(children=[], marker=node.marker)
            state.placeholders.extend((
                ProtectedInline(placeholder=begin, node=template),
                ProtectedInline(placeholder=end, node=template),
            ))
            out.append(f"{begin}{inner_text}{end}")
        elif isinstance(node, InlineStrike):
            inner_text = _protect_walk(node.children, state)
            begin, end = state.next_boundaries("STRIKE")
            template = InlineStrike(children=[])
            state.placeholders.extend((
                ProtectedInline(placeholder=begin, node=template),
                ProtectedInline(placeholder=end, node=template),
            ))
            out.append(f"{begin}{inner_text}{end}")
        elif isinstance(node, InlineSoftBreak):
            out.append("\n")
        elif isinstance(node, InlineHardBreak):
            out.append("  \n")
        else:
            # Defensive: anything else passes through as-is via str().
            out.append(str(node))

    return "".join(out)


def restore_inline_text(
    text: str, placeholders: list[ProtectedInline], *, target_locale: bool = False
) -> str:
    """Restore the original markdown by replacing placeholders with rendered atoms.

    Used in tests and for diagnostics; the main pipeline uses
    ``reinsert.py``'s placeholder substitution at the AST level.
    """
    from ydbdoc_review.rendering.markdown_renderer import _render_inline_node

    result = text
    by_marker = {placeholder.placeholder: placeholder.node for placeholder in placeholders}
    for marker, node in by_marker.items():
        if "BEGIN_" not in marker:
            continue
        prefix, identity = marker[1:-1].split("BEGIN_", 1)
        end = f"⟦{prefix}END_{identity}⟧"
        if result.count(marker) != 1 or result.count(end) != 1:
            raise ValueError("missing, duplicated, or reordered structural boundary")
        if result.index(marker) > result.index(end):
            raise ValueError("crossing structural boundary")
        if isinstance(node, InlineLink):
            href = node.href
            if target_locale and href.startswith("/ru/"):
                href = "/en/" + href.removeprefix("/ru/")
            title = f' "{node.title}"' if node.title else ""
            opening, closing = "[", f"]({href}{title})"
        elif isinstance(node, InlineImage):
            title = f' "{node.title}"' if node.title else ""
            size = ""
            if node.width is not None or node.height is not None:
                size = f" ={node.width or ''}x{node.height or ''}"
            opening, closing = "![", f"]({node.src}{size}{title})"
        elif isinstance(node, (InlineEmphasis, InlineStrong, InlineStrike)):
            marker_text = node.marker
            opening = closing = marker_text
        else:
            raise ValueError("unsupported structural boundary template")
        result = result.replace(marker, opening, 1).replace(end, closing, 1)
    for p in placeholders:
        if "BEGIN_" in p.placeholder or "END_" in p.placeholder:
            continue
        if (
            isinstance(p.node, InlineLink)
            and not p.node.children
            and p.node.href
        ):
            replacement = p.node.href
        elif isinstance(p.node, InlineImage) and not p.node.alt:
            from ydbdoc_review.rendering.markdown_renderer import _render_inline_node

            replacement = _render_inline_node(p.node)
        else:
            replacement = _render_inline_node(p.node)
        result = result.replace(p.placeholder, replacement, 1)
    return result
