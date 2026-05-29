"""Segment data types used by the translation pipeline."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

from ydbdoc_review.parsing.ast_types import InlineNode


class SegmentKind(str, Enum):
    """Kind of translatable unit."""

    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    TABLE_HEADER_CELL = "table_header_cell"
    TABLE_BODY_CELL = "table_body_cell"
    BLOCKQUOTE_PARAGRAPH = "blockquote_paragraph"
    TAB_TITLE = "tab_title"
    TERM_DEFINITION = "term_definition"


class ProtectedInline(BaseModel):
    """One inline atom replaced by a placeholder."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    placeholder: str            # e.g. "⟦C1⟧"
    node: Any                   # InlineNode (kept as Any to avoid discriminator issues)


class Segment(BaseModel):
    """A translatable unit extracted from a document."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    id: str                          # stable, e.g. "s001"
    kind: SegmentKind
    path: list[str]                  # breadcrumbs, e.g. ["Examples", "table:Options"]
    text: str                        # markdown with ⟦K1⟧ placeholders
    placeholders: list[ProtectedInline]
    # Pointer back to the AST so we can write the translation back.
    # We store (kind, path_indices) instead of a node ref to keep things serialisable.
    ast_path: list[int]              # indices into the document tree

