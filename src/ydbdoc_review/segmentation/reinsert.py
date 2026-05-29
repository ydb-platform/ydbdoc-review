"""Re-insert translated segment text back into the AST."""

from __future__ import annotations

from ydbdoc_review.parsing.ast_types import (
    BlockNode,
    Document,
    Heading,
    InlineNode,
    InlineText,
    ListItem,
    Paragraph,
    Table,
    TermDefinition,
    YfmCut,
    YfmIf,
    YfmNote,
    YfmTab,
    YfmTabs,
)
from ydbdoc_review.parsing.inline_parser import parse_inline_text
from ydbdoc_review.segmentation.types import ProtectedInline, Segment, SegmentKind


class ReinsertError(Exception):
    """Raised when a segment cannot be re-inserted into the AST."""


def reinsert_segments(
    doc: Document, segments: list[Segment], translations: dict[str, str]
) -> Document:
    """Return a new Document with each segment's inline children replaced.

    ``translations`` maps segment id → translated text (still containing
    placeholders like ⟦C1⟧). Segments whose id is not in ``translations`` keep
    their original text.

    The function does **not** mutate the input Document in place — it walks
    the segments list, applies changes in order, but since the AST is shared,
    the input doc is modified. (Pydantic models are mutable.) Callers that
    need immutability should deepcopy first.
    """
    for seg in segments:
        translated = translations.get(seg.id, seg.text)
        new_inline = _build_inline_from_translation(translated, seg.placeholders)
        _set_inline_at_ast_path(doc, seg, new_inline)
    return doc


def _build_inline_from_translation(
    text: str, placeholders: list[ProtectedInline]
) -> list[InlineNode]:
    """Parse translated text and substitute placeholders for original nodes."""
    # Parse the text as inline markdown — placeholders ⟦K1⟧ will become InlineText.
    nodes = parse_inline_text(text)
    # Replace placeholder text nodes with the original protected nodes.
    mapping = {p.placeholder: p.node for p in placeholders}
    return _substitute_placeholders(nodes, mapping)


def _substitute_placeholders(
    nodes: list[InlineNode], mapping: dict[str, InlineNode]
) -> list[InlineNode]:
    """Walk inline nodes and replace any text containing placeholders.

    A single InlineText("foo ⟦C1⟧ bar ⟦L1⟧ baz") becomes:
       [InlineText("foo "), <original C1>, InlineText(" bar "), <original L1>, InlineText(" baz")]
    """
    out: list[InlineNode] = []
    for node in nodes:
        if isinstance(node, InlineText):
            out.extend(_split_text_by_placeholders(node.content, mapping))
        elif hasattr(node, "children") and isinstance(node.children, list):
            # Descend into emphasis/strong/link.
            node.children = _substitute_placeholders(node.children, mapping)
            out.append(node)
        else:
            out.append(node)
    return out


def _split_text_by_placeholders(
    text: str, mapping: dict[str, InlineNode]
) -> list[InlineNode]:
    """Split a text string at placeholder markers and substitute originals."""
    if not mapping:
        return [InlineText(content=text)] if text else []

    # Build a single regex over placeholder strings.
    import re

    if not mapping:
        return [InlineText(content=text)]
    # Sort by length descending so longer placeholders match first
    # (defensive; in practice all placeholders are short and unique).
    keys_sorted = sorted(mapping.keys(), key=len, reverse=True)
    pattern = "|".join(re.escape(k) for k in keys_sorted)
    parts = re.split(f"({pattern})", text)

    out: list[InlineNode] = []
    for part in parts:
        if not part:
            continue
        if part in mapping:
            out.append(mapping[part])
        else:
            out.append(InlineText(content=part))
    return out


# --- AST navigation ---


def _set_inline_at_ast_path(
    doc: Document, seg: Segment, new_inline: list[InlineNode]
) -> None:
    kind = seg.kind
    path = seg.ast_path

    if kind == SegmentKind.PARAGRAPH:
        node = _navigate_to_doc_index(doc, path)
        if not isinstance(node, Paragraph):
            raise ReinsertError(
                f"Expected Paragraph at {path}, got {type(node).__name__}"
            )
        node.children = new_inline
    elif kind == SegmentKind.HEADING:
        node = _navigate_to_doc_index(doc, path)
        if not isinstance(node, Heading):
            raise ReinsertError(
                f"Expected Heading at {path}, got {type(node).__name__}"
            )
        node.children = new_inline
    elif kind == SegmentKind.TERM_DEFINITION:
        node = _navigate_to_doc_index(doc, path)
        if not isinstance(node, TermDefinition):
            raise ReinsertError(
                f"Expected TermDefinition at {path}, got {type(node).__name__}"
            )
        node.children = new_inline
    elif kind == SegmentKind.TABLE_HEADER_CELL:
        # path: [..., "header", col_idx]
        table = _navigate_to_doc_index(doc, path[:-2])
        if not isinstance(table, Table):
            raise ReinsertError(f"Expected Table, got {type(table).__name__}")
        col = path[-1]
        if not isinstance(col, int):
            raise ReinsertError(f"Bad col index in {path}")
        table.header.cells[col].children = new_inline
    elif kind == SegmentKind.TABLE_BODY_CELL:
        # path: [..., "row", row_idx, col_idx]
        table = _navigate_to_doc_index(doc, path[:-3])
        if not isinstance(table, Table):
            raise ReinsertError(f"Expected Table, got {type(table).__name__}")
        row_idx, col_idx = path[-2], path[-1]
        if not isinstance(row_idx, int) or not isinstance(col_idx, int):
            raise ReinsertError(f"Bad row/col index in {path}")
        table.rows[row_idx].cells[col_idx].children = new_inline
    elif kind == SegmentKind.TAB_TITLE:
        # path: [..., yfm_tabs_idx, tab_idx, "title"]
        tabs = _navigate_to_doc_index(doc, path[:-2])
        if not isinstance(tabs, YfmTabs):
            raise ReinsertError(f"Expected YfmTabs, got {type(tabs).__name__}")
        tab_idx = path[-2]
        if not isinstance(tab_idx, int):
            raise ReinsertError(f"Bad tab index in {path}")
        tabs.children[tab_idx].title = new_inline
    elif kind == SegmentKind.LIST_ITEM:
        node = _navigate_to_doc_index(doc, path)
        if isinstance(node, ListItem):
            if node.children and isinstance(node.children[0], Paragraph):
                node.children[0].children = new_inline
            else:
                raise ReinsertError(
                    "List item without leading paragraph cannot accept inline."
                )
        else:
            raise ReinsertError(
                f"Expected ListItem at {path}, got {type(node).__name__}"
            )
    elif kind == SegmentKind.BLOCKQUOTE_PARAGRAPH:
        node = _navigate_to_doc_index(doc, path)
        if not isinstance(node, Paragraph):
            raise ReinsertError(
                f"Expected Paragraph at {path}, got {type(node).__name__}"
            )
        node.children = new_inline
    else:
        raise ReinsertError(f"Unsupported segment kind: {kind}")


def _navigate_to_doc_index(doc: Document, path: list) -> object:
    """Walk a numeric path through children/branches; ignore non-int markers."""
    node: object = doc
    for step in path:
        if not isinstance(step, int):
            raise ReinsertError(
                f"Unexpected non-int step {step!r} in path {path}; "
                "use a dedicated helper for typed paths."
            )
        if isinstance(node, Document):
            node = node.children[step]
        elif isinstance(node, YfmIf):
            node = node.branches[step]
        elif hasattr(node, "children") and isinstance(
            getattr(node, "children"), list
        ):
            node = node.children[step]  # type: ignore[index]
        else:
            raise ReinsertError(
                f"Cannot descend into {type(node).__name__} at index {step}"
            )
    return node