"""Markdown → IR parser.

Wraps markdown-it-py and converts its flat token stream into our IR tree.
"""

from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.front_matter import front_matter_plugin

from ydbdoc_review.parsing.yfm_plugins.variables import yfm_variable_plugin


from ydbdoc_review.parsing.ast_types import (
    BlockNode,
    BlockQuote,
    BulletList,
    Document,
    FencedCode,
    Heading,
    HTMLBlock,
    IndentedCode,
    InlineCode,
    InlineEmphasis,
    InlineHardBreak,
    InlineHTML,
    InlineImage,
    InlineLink,
    InlineNode,
    InlineSoftBreak,
    InlineStrong,
    InlineText,
    InlineVariable,  # NEW
    ListItem,
    OrderedList,
    Paragraph,
    Table,
    TableCell,
    TableRow,
    ThematicBreak,
)



def create_parser() -> MarkdownIt:
    """Create a markdown-it parser configured for YDB documentation."""
    md = MarkdownIt("commonmark", {"html": True, "breaks": False, "linkify": False})
    md.enable("table")
    md.enable("strikethrough")
    md.use(front_matter_plugin)
    md.use(yfm_variable_plugin)
    return md



class _TokenStream:
    """Cursor over a flat list of markdown-it tokens."""

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token | None:
        if self.pos >= len(self.tokens):
            return None
        return self.tokens[self.pos]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, type_: str) -> Token:
        tok = self.advance()
        if tok.type != type_:
            raise ValueError(f"Expected {type_}, got {tok.type} at pos {self.pos - 1}")
        return tok

    def eof(self) -> bool:
        return self.pos >= len(self.tokens)


def parse_markdown(text: str) -> Document:
    """Parse markdown text into a Document IR tree."""
    md = create_parser()
    tokens = md.parse(text)
    stream = _TokenStream(tokens)
    return _parse_document(stream)


def _parse_document(stream: _TokenStream) -> Document:
    children: list[BlockNode] = []
    front_matter: str | None = None

    while not stream.eof():
        tok = stream.peek()
        assert tok is not None
        if tok.type == "front_matter":
            front_matter = tok.content
            stream.advance()
            continue
        block = _parse_block(stream)
        if block is not None:
            children.append(block)

    return Document(children=children, front_matter=front_matter)


def _parse_block(stream: _TokenStream) -> BlockNode | None:
    tok = stream.peek()
    if tok is None:
        return None

    t = tok.type
    if t == "paragraph_open":
        return _parse_paragraph(stream)
    if t == "heading_open":
        return _parse_heading(stream)
    if t == "fence":
        return _parse_fence(stream)
    if t == "code_block":
        return _parse_indented_code(stream)
    if t == "hr":
        return _parse_hr(stream)
    if t == "blockquote_open":
        return _parse_blockquote(stream)
    if t == "bullet_list_open":
        return _parse_bullet_list(stream)
    if t == "ordered_list_open":
        return _parse_ordered_list(stream)
    if t == "html_block":
        return _parse_html_block(stream)
    if t == "table_open":
        return _parse_table(stream)

    # Unknown token — skip with a warning later. For now, advance to avoid infinite loop.
    raise ValueError(f"Unsupported block token: {t} (content={tok.content!r})")


def _parse_paragraph(stream: _TokenStream) -> Paragraph:
    stream.expect("paragraph_open")
    inline_tok = stream.expect("inline")
    stream.expect("paragraph_close")
    children = _parse_inline_children(inline_tok.children or [])
    return Paragraph(children=children)


def _parse_heading(stream: _TokenStream) -> Heading:
    open_tok = stream.expect("heading_open")
    level = int(open_tok.tag[1])  # h1 -> 1
    inline_tok = stream.expect("inline")
    stream.expect("heading_close")
    children = _parse_inline_children(inline_tok.children or [])

    # Extract YFM anchor {#anchor-id} from the trailing text.
    anchor = None
    if children and isinstance(children[-1], InlineText):
        text = children[-1].content
        # Match " {#anchor-id}" at the end.
        import re

        m = re.search(r"\s*\{#([A-Za-z0-9_\-]+)\}\s*$", text)
        if m:
            anchor = m.group(1)
            new_text = text[: m.start()].rstrip()
            if new_text:
                children[-1] = InlineText(content=new_text)
            else:
                children.pop()

    return Heading(level=level, children=children, anchor=anchor)


def _parse_fence(stream: _TokenStream) -> FencedCode:
    tok = stream.expect("fence")
    # markup is the fence character sequence, e.g. "```" or "~~~~".
    fence_char = "`" if tok.markup.startswith("`") else "~"
    fence_len = len(tok.markup)
    content = tok.content
    # markdown-it includes the trailing newline; preserve as-is.
    return FencedCode(
        info=tok.info,
        content=content,
        fence_char=fence_char,
        fence_len=fence_len,
    )


def _parse_indented_code(stream: _TokenStream) -> IndentedCode:
    tok = stream.expect("code_block")
    return IndentedCode(content=tok.content)


def _parse_hr(stream: _TokenStream) -> ThematicBreak:
    tok = stream.expect("hr")
    marker = tok.markup or "---"
    return ThematicBreak(marker=marker)


def _parse_blockquote(stream: _TokenStream) -> BlockQuote:
    stream.expect("blockquote_open")
    children: list[BlockNode] = []
    while True:
        tok = stream.peek()
        if tok is None or tok.type == "blockquote_close":
            break
        block = _parse_block(stream)
        if block is not None:
            children.append(block)
    stream.expect("blockquote_close")
    return BlockQuote(children=children)


def _parse_bullet_list(stream: _TokenStream) -> BulletList:
    open_tok = stream.expect("bullet_list_open")
    marker = open_tok.markup or "-"
    items: list[ListItem] = []
    while True:
        tok = stream.peek()
        if tok is None or tok.type == "bullet_list_close":
            break
        items.append(_parse_list_item(stream, marker))
    stream.expect("bullet_list_close")
    return BulletList(children=items, marker=marker, tight=_detect_tight(open_tok))  # type: ignore[arg-type]


def _parse_ordered_list(stream: _TokenStream) -> OrderedList:
    open_tok = stream.expect("ordered_list_open")
    start = int(open_tok.attrGet("start") or 1)
    delimiter = open_tok.markup or "."  # "." or ")"
    items: list[ListItem] = []
    while True:
        tok = stream.peek()
        if tok is None or tok.type == "ordered_list_close":
            break
        items.append(_parse_list_item(stream, str(start)))
    stream.expect("ordered_list_close")
    return OrderedList(
        children=items,
        start=start,
        delimiter=delimiter,  # type: ignore[arg-type]
        tight=_detect_tight(open_tok),
    )


def _parse_list_item(stream: _TokenStream, marker: str) -> ListItem:
    stream.expect("list_item_open")
    children: list[BlockNode] = []
    while True:
        tok = stream.peek()
        if tok is None or tok.type == "list_item_close":
            break
        block = _parse_block(stream)
        if block is not None:
            children.append(block)
    stream.expect("list_item_close")
    return ListItem(children=children, marker=marker)


def _detect_tight(open_tok: Token) -> bool:
    # markdown-it sets meta["tight"] / hidden paragraphs for tight lists; default True.
    return True


def _parse_html_block(stream: _TokenStream) -> HTMLBlock:
    tok = stream.expect("html_block")
    return HTMLBlock(content=tok.content)


def _parse_table(stream: _TokenStream) -> Table:
    stream.expect("table_open")
    # thead
    stream.expect("thead_open")
    stream.expect("tr_open")
    header_cells: list[TableCell] = []
    aligns: list[str] = []
    while True:
        tok = stream.peek()
        if tok is None or tok.type == "tr_close":
            break
        cell_open = stream.advance()
        if cell_open.type != "th_open":
            raise ValueError(f"Expected th_open, got {cell_open.type}")
        align = _extract_align(cell_open)
        aligns.append(align)
        inline_tok = stream.expect("inline")
        stream.expect("th_close")
        header_cells.append(
            TableCell(
                children=_parse_inline_children(inline_tok.children or []),
                is_header=True,
                align=align,  # type: ignore[arg-type]
            )
        )
    stream.expect("tr_close")
    stream.expect("thead_close")
    header_row = TableRow(cells=header_cells)

    # tbody (optional if there are no body rows? markdown-it always emits it for tables)
    rows: list[TableRow] = []
    if stream.peek() and stream.peek().type == "tbody_open":  # type: ignore[union-attr]
        stream.advance()
        while True:
            tok = stream.peek()
            if tok is None or tok.type == "tbody_close":
                break
            stream.expect("tr_open")
            cells: list[TableCell] = []
            cell_idx = 0
            while True:
                tok2 = stream.peek()
                if tok2 is None or tok2.type == "tr_close":
                    break
                cell_open = stream.advance()
                if cell_open.type != "td_open":
                    raise ValueError(f"Expected td_open, got {cell_open.type}")
                align = aligns[cell_idx] if cell_idx < len(aligns) else "none"
                inline_tok = stream.expect("inline")
                stream.expect("td_close")
                cells.append(
                    TableCell(
                        children=_parse_inline_children(inline_tok.children or []),
                        is_header=False,
                        align=align,  # type: ignore[arg-type]
                    )
                )
                cell_idx += 1
            stream.expect("tr_close")
            rows.append(TableRow(cells=cells))
        stream.expect("tbody_close")

    stream.expect("table_close")
    return Table(header=header_row, rows=rows, aligns=aligns)  # type: ignore[arg-type]


def _extract_align(cell_open: Token) -> str:
    style = cell_open.attrGet("style") or ""
    if "left" in style:
        return "left"
    if "right" in style:
        return "right"
    if "center" in style:
        return "center"
    return "none"


# --- Inline parsing ---


def _parse_inline_children(tokens: list[Token]) -> list[InlineNode]:
    """Convert a flat list of inline tokens into a tree of InlineNode."""
    stream = _TokenStream(tokens)
    return _parse_inline_until(stream, close_type=None)


def _parse_inline_until(stream: _TokenStream, close_type: str | None) -> list[InlineNode]:
    children: list[InlineNode] = []
    while not stream.eof():
        tok = stream.peek()
        assert tok is not None
        if close_type is not None and tok.type == close_type:
            break

        t = tok.type
        if t == "text":
            stream.advance()
            children.append(InlineText(content=tok.content))
        elif t == "code_inline":
            stream.advance()
            marker_len = len(tok.markup) if tok.markup else 1
            children.append(InlineCode(content=tok.content, marker_len=marker_len))
        elif t == "softbreak":
            stream.advance()
            children.append(InlineSoftBreak())
        elif t == "hardbreak":
            stream.advance()
            children.append(InlineHardBreak())
        elif t == "html_inline":
            stream.advance()
            children.append(InlineHTML(content=tok.content))
        elif t == "em_open":
            stream.advance()
            inner = _parse_inline_until(stream, "em_close")
            stream.expect("em_close")
            marker = tok.markup if tok.markup in ("*", "_") else "*"
            children.append(InlineEmphasis(children=inner, marker=marker))  # type: ignore[arg-type]
        elif t == "strong_open":
            stream.advance()
            inner = _parse_inline_until(stream, "strong_close")
            stream.expect("strong_close")
            marker = tok.markup if tok.markup in ("**", "__") else "**"
            children.append(InlineStrong(children=inner, marker=marker))  # type: ignore[arg-type]
        elif t == "link_open":
            stream.advance()
            href = tok.attrGet("href") or ""
            title = tok.attrGet("title")
            inner = _parse_inline_until(stream, "link_close")
            stream.expect("link_close")
            children.append(InlineLink(href=href, title=title, children=inner))
        elif t == "image":
            stream.advance()
            src = tok.attrGet("src") or ""
            title = tok.attrGet("title")
            # Image alt is in tok.content (text of inner tokens).
            alt = tok.content
            children.append(InlineImage(src=src, title=title, alt=alt))
        elif t == "s_open":
            # Strikethrough: not yet modelled, treat as text by collecting inner.
            stream.advance()
            _ = _parse_inline_until(stream, "s_close")
            stream.expect("s_close")
            # For now, skip strikethrough rendering — preserve content as text.
            # TODO: add InlineStrike node.
        elif t == "yfm_variable":
            stream.advance()
            children.append(
                InlineVariable(name=tok.content, raw=tok.markup)
            )
        else:
            raise ValueError(f"Unsupported inline token: {t} (content={tok.content!r})")

    return children
