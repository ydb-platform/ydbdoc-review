"""Parse a string as inline markdown only (no block constructs)."""

from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.token import Token

from ydbdoc_review.parsing.ast_types import InlineNode


def parse_inline_text(text: str) -> list[InlineNode]:
    """Parse the given string as inline markdown content.

    Used to re-insert translated segment text back into AST: the segment text
    contains placeholders like ⟦C1⟧, which we keep as plain text inline nodes
    (they'll be replaced with the original protected atoms later).
    """
    # Local import to avoid circularity: markdown_parser uses ast_types too.
    from ydbdoc_review.parsing.markdown_parser import (
        _parse_inline_children,
        create_parser,
    )

    md: MarkdownIt = create_parser()
    # parseInline returns a list with a single 'inline'-like wrapper token.
    tokens: list[Token] = md.parseInline(text, {})
    if not tokens or not tokens[0].children:
        return []
    return _parse_inline_children(tokens[0].children)

