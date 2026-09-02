"""Markdown → IR parser.

Wraps markdown-it-py and converts its flat token stream into our IR tree.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.front_matter import front_matter_plugin

from ydbdoc_review.parsing.ast_types import (
    AmbiguousYfmStructureError,
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
    InlineStrike,
    InlineStrong,
    InlineTermRef,
    InlineText,
    InlineVariable,
    ListItem,
    OrderedList,
    Paragraph,
    SourceSpan,
    Table,
    TableCell,
    TableRow,
    TermDefinition,
    ThematicBreak,
    YfmCut,
    YfmIf,
    YfmIfBranch,
    YfmInclude,
    YfmNote,
    YfmTab,
    YfmTabs,
)
from ydbdoc_review.parsing.front_matter import parse_front_matter_with_spans
from ydbdoc_review.parsing.yfm_plugins.conditionals import yfm_if_plugin
from ydbdoc_review.parsing.yfm_plugins.cuts import yfm_cut_plugin
from ydbdoc_review.parsing.yfm_plugins.image_size import yfm_image_size_plugin  # NEW
from ydbdoc_review.parsing.yfm_plugins.includes import yfm_include_plugin
from ydbdoc_review.parsing.yfm_plugins.link_with_variable import (
    yfm_link_with_variable_plugin,  # NEW
)
from ydbdoc_review.parsing.yfm_plugins.notes import yfm_note_plugin
from ydbdoc_review.parsing.yfm_plugins.tables import yfm_table_plugin
from ydbdoc_review.parsing.yfm_plugins.tabs import yfm_tabs_plugin
from ydbdoc_review.parsing.yfm_plugins.terms import yfm_terms_plugin
from ydbdoc_review.parsing.yfm_plugins.variables import yfm_variable_plugin

_PARSE_SOURCE: ContextVar[str | None] = ContextVar("markdown_parse_source", default=None)


@dataclass(frozen=True, slots=True)
class ParserSpanRecord:
    kind: str
    start_byte: int
    end_byte: int
    descriptor: str
    translatable_spans: tuple[tuple[int, int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedMarkdownSourceMap:
    containers: tuple[ParserSpanRecord, ...]
    non_prose: tuple[ParserSpanRecord, ...]


def create_parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True, "breaks": False, "linkify": False})
    md.enable("table")
    md.enable("strikethrough")
    md.use(front_matter_plugin)
    md.use(yfm_link_with_variable_plugin)  # must be early (mutates source)
    md.use(yfm_variable_plugin)
    md.use(yfm_note_plugin)
    md.use(yfm_table_plugin)
    md.use(yfm_tabs_plugin)
    md.use(yfm_include_plugin)
    md.use(yfm_if_plugin)
    md.use(yfm_cut_plugin)
    md.use(yfm_terms_plugin)
    md.use(yfm_image_size_plugin)
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


def _source_index_from_lines(text: str) -> tuple[int, ...]:
    starts = [0]
    position = 0
    for line in text.splitlines(keepends=True):
        position += len(line.encode("utf-8"))
        starts.append(position)
    if not text or starts[-1] != len(text.encode("utf-8")):
        starts.append(len(text.encode("utf-8")))
    return tuple(starts)


def _line_index_for_byte(line_starts: tuple[int, ...], byte_offset: int) -> int:
    """Return the 0-based line that owns ``byte_offset`` within ``line_starts``."""
    if byte_offset < 0 or byte_offset > line_starts[-1]:
        raise ValueError("source_map_incomplete:byte_offset")
    # Last entry is EOF. Treat EOF as belonging to the final content line when
    # present; otherwise reject (empty document).
    if byte_offset == line_starts[-1]:
        if len(line_starts) < 2:
            raise ValueError("source_map_incomplete:byte_offset")
        return len(line_starts) - 2
    low = 0
    high = len(line_starts) - 2
    while low <= high:
        mid = (low + high) // 2
        if line_starts[mid] <= byte_offset < line_starts[mid + 1]:
            return mid
        if byte_offset < line_starts[mid]:
            high = mid - 1
        else:
            low = mid + 1
    raise ValueError("source_map_incomplete:byte_offset")


def _line_content_bounds(
    raw: bytes, line_starts: tuple[int, ...], line_index: int
) -> tuple[int, int, int]:
    """Return ``(line_start, content_start, content_end)`` for a 0-based line.

    ``content_start`` matches StateBlock ``bMarks[line] + tShift[line]``:
    the first non-indent byte on the line (or ``line_start`` when empty).
    ``content_end`` matches ``eMarks[line]`` (excludes the line terminator).
    """
    if line_index < 0 or line_index + 1 >= len(line_starts):
        raise ValueError("source_map_incomplete:line")
    line_start = line_starts[line_index]
    next_line_start = line_starts[line_index + 1]
    line_bytes = raw[line_start:next_line_start]
    if line_bytes.endswith(b"\r\n"):
        content_end = next_line_start - 2
    elif line_bytes.endswith(b"\n"):
        content_end = next_line_start - 1
    else:
        content_end = next_line_start
    content_start = line_start
    while content_start < content_end and raw[content_start] in (0x20, 0x09):
        content_start += 1
    return line_start, content_start, content_end


def _record_from_token_map(
    token: Token,
    line_starts: tuple[int, ...],
    *,
    kind: str,
    descriptor: str,
    source_span: SourceSpan | None = None,
    inherited_map: tuple[int, int] | None = None,
) -> ParserSpanRecord:
    listed_yfm = token.type in {
        "yfm_if_open",
        "yfm_if_branch_open",
        "yfm_if_branch_close",
        "yfm_if_close",
        "yfm_note_open",
        "yfm_note_close",
        "yfm_cut_open",
        "yfm_cut_close",
        "yfm_tabs_open",
        "yfm_tab_open",
        "yfm_tab_close",
        "yfm_tabs_close",
        "yfm_include",
    }
    meta_span = (token.meta or {}).get("source_span")
    if meta_span is not None:
        source_span = SourceSpan.model_validate(meta_span)
    if listed_yfm and source_span is None:
        raise ValueError(f"source_map_incomplete:{kind}")
    if source_span is not None:
        start, end = source_span.byte_start, source_span.byte_end
    elif (token.map is not None and len(token.map) == 2) or inherited_map is not None:
        start_line, end_line = (
            (token.map[0], token.map[1]) if token.map is not None else inherited_map
        )
        if (
            start_line < 0
            or end_line < start_line
            or end_line >= len(line_starts)
        ):
            raise ValueError(f"source_map_incomplete:{kind}")
        start, end = line_starts[start_line], line_starts[end_line]
    else:
        raise ValueError(f"source_map_incomplete:{kind}")
    if start < 0 or end < start or end > line_starts[-1]:
        raise ValueError(f"source_map_incomplete:{kind}")
    virtual_close = token.type in {"yfm_if_branch_close", "yfm_tab_close"}
    if listed_yfm and virtual_close and start != end:
        raise ValueError(f"source_map_incomplete:{kind}")
    if listed_yfm and not virtual_close and start == end:
        raise ValueError(f"source_map_incomplete:{kind}")
    if listed_yfm and source_span is not None:
        try:
            derived_line = _line_index_for_byte(line_starts, start)
        except ValueError as exc:
            raise ValueError(f"source_map_incomplete:{kind}") from exc
        # Owned line must come from StateBlock identity (token.map[0] or plugin
        # meta owned_line). Never fall back to derived_line: that trusts the
        # span under test and lets closes with map=None relocate freely.
        meta_owned = (token.meta or {}).get("owned_line")
        if token.map is not None and len(token.map) >= 1:
            owned_line = token.map[0]
            if meta_owned is not None and meta_owned != owned_line:
                raise ValueError(f"source_map_incomplete:{kind}")
        elif isinstance(meta_owned, int) and meta_owned >= 0:
            owned_line = meta_owned
        else:
            raise ValueError(f"source_map_incomplete:{kind}")
        if derived_line != owned_line or source_span.line != owned_line + 1:
            raise ValueError(f"source_map_incomplete:{kind}")
        source_text = _PARSE_SOURCE.get() or ""
        raw = source_text.encode("utf-8")
        try:
            line_start, content_start, content_end = _line_content_bounds(
                raw, line_starts, owned_line
            )
        except ValueError as exc:
            raise ValueError(f"source_map_incomplete:{kind}") from exc
        if virtual_close:
            # Exact bMarks[L] + tShift[L] boundary; anywhere-on-line is fail-open.
            if start != content_start:
                raise ValueError(f"source_map_incomplete:{kind}")
        elif token.type == "yfm_tab_open":
            # Tab open owns the full multi-line slice from bMarks[header].
            if start != line_start or end < start:
                raise ValueError(f"source_map_incomplete:{kind}")
        elif not (start == content_start and start < end <= content_end):
            # Single-line physical markers begin at bMarks+tShift and stay on L.
            raise ValueError(f"source_map_incomplete:{kind}")
    title_span = (token.meta or {}).get("title_span")
    if token.type in {"yfm_note_open", "yfm_cut_open", "yfm_tab_open"} and title_span is not None:
        if token.type == "yfm_note_open":
            segment_kind = "note_title"
        elif token.type == "yfm_cut_open":
            segment_kind = "cut_title"
        else:
            segment_kind = "tab_title"
        parsed_title_span = SourceSpan.model_validate(title_span)
        translatable_spans = ((parsed_title_span.byte_start, parsed_title_span.byte_end, segment_kind),)
    else:
        translatable_spans = ()
    if token.type == "front_matter":
        if token.map is None:
            raise ValueError("source_map_invalid_front_matter:token")
        _fields, front_records = parse_front_matter_with_spans(token.content)
        body_start = line_starts[token.map[0] + 1]
        translatable_spans = tuple(
            (
                body_start + record.start_byte,
                body_start + record.end_byte,
                f"front_matter:{record.key}",
            )
            for record in front_records
        )
        descriptor = f"{descriptor}|selected={tuple((record.key, record.style) for record in front_records)!r}"
    if token.type == "yfm_note_open":
        has_title = (token.meta or {}).get("title") is not None
        if has_title != bool(translatable_spans):
            raise ValueError("source_map_invalid_translatable_span:yfm_note_open")
    if token.type == "yfm_cut_open" and not translatable_spans:
        raise ValueError("source_map_invalid_translatable_span:yfm_cut_open")
    if token.type == "yfm_tab_open" and not translatable_spans:
        raise ValueError("source_map_invalid_translatable_span:yfm_tab_open")
    if title_span is not None and token.type not in {
        "yfm_note_open",
        "yfm_cut_open",
        "yfm_tab_open",
    }:
        raise ValueError(f"source_map_invalid_translatable_span:{token.type}")
    record = ParserSpanRecord(kind, start, end, descriptor, translatable_spans)
    _canonical_source_slice(_PARSE_SOURCE.get() or "", record)
    return record


def _canonical_source_slice(source_text: str, record: ParserSpanRecord) -> bytes:
    raw = source_text.encode("utf-8")
    if not 0 <= record.start_byte <= record.end_byte <= len(raw):
        raise ValueError(f"source_map_invalid_translatable_span:{record.kind}")
    cursor = record.start_byte
    output = bytearray()
    for start, end, segment_kind in record.translatable_spans:
        role_valid = (
            (record.kind == "yfm_note_open" and segment_kind == "note_title")
            or (record.kind == "yfm_cut_open" and segment_kind == "cut_title")
            or (record.kind == "yfm_tab_open" and segment_kind == "tab_title")
            or (
                record.kind == "front_matter"
                and segment_kind in {
                    "front_matter:title",
                    "front_matter:description",
                }
            )
        )
        if (
            not role_valid
            or start < cursor
            or end < start
            or end > record.end_byte
        ):
            raise ValueError(f"source_map_invalid_translatable_span:{record.kind}")
        output.extend(raw[cursor:start])
        output.extend(b"\x00YDBDOC_TRANSLATABLE:")
        output.extend(segment_kind.encode("ascii"))
        output.extend(b"\x00")
        cursor = end
    output.extend(raw[cursor:record.end_byte])
    return bytes(output)


def _token_descriptor(token: Token) -> str:
    meta = token.meta or {}
    markup = token.markup
    if token.type in {"yfm_note_open", "yfm_cut_open"}:
        markup = token.type
    variant = "|".join(
        f"{key}={meta[key] is not None!r}" if key == "title" else f"{key}={meta[key]!r}"
        for key in (
            "variant",
            "condition",
            "branch_kind",
            "note_type",
            "title",
            "notitle",
            "term_id",
        )
        if key in meta
    )
    return f"{token.type}|{token.level}|{token.tag}|{markup}|{variant}"


_CONTAINER_TOKEN_TYPES = frozenset(
    {
        "paragraph_open",
        "heading_open",
        "blockquote_open",
        "bullet_list_open",
        "ordered_list_open",
        "list_item_open",
        "table_open",
        "thead_open",
        "tbody_open",
        "tr_open",
        "th_open",
        "td_open",
        "yfm_note_open",
        "yfm_tabs_open",
        "yfm_tab_open",
        "yfm_if_open",
        "yfm_if_branch_open",
        "yfm_cut_open",
        "term_definition_open",
        "yfm_if_branch_close",
        "yfm_if_close",
        "yfm_note_close",
        "yfm_cut_close",
        "yfm_tab_close",
        "yfm_tabs_close",
    }
)

_NON_PROSE_TOKEN_TYPES = frozenset(
    {"front_matter", "fence", "code_block", "html_block", "yfm_include"}
)


def _build_parser_source_map(
    text: str, tokens: list[Token]
) -> ParsedMarkdownSourceMap:
    line_starts = _source_index_from_lines(text)
    containers: list[ParserSpanRecord] = []
    non_prose: list[ParserSpanRecord] = []
    map_stack: list[tuple[int, int]] = []
    for token in tokens:
        descriptor = _token_descriptor(token)
        token_map = (
            (token.map[0], token.map[1])
            if token.map is not None and len(token.map) == 2
            else None
        )
        inherited_map = map_stack[-1] if map_stack else None
        if token.type in _CONTAINER_TOKEN_TYPES:
            container_record = _record_from_token_map(
                token,
                line_starts,
                kind=token.type,
                descriptor=descriptor,
                inherited_map=inherited_map,
            )
            containers.append(container_record)
        if token.type in _NON_PROSE_TOKEN_TYPES:
            non_prose.append(
                _record_from_token_map(
                    token, line_starts, kind=token.type, descriptor=descriptor
                )
            )
        if (
            token.type.startswith("yfm_")
            and token.type not in {"yfm_tab_open", "yfm_include"}
        ):
            span = SourceSpan.model_validate(token.meta["source_span"])
            if span.byte_start != span.byte_end:
                non_prose.append(
                    _record_from_token_map(
                        token,
                        line_starts,
                        kind=token.type,
                        descriptor=descriptor,
                        source_span=span,
                    )
                )
        if token.type == "yfm_tab_open":
            title = SourceSpan.model_validate(token.meta["title_span"])
            physical = ParserSpanRecord(
                token.type,
                container_record.start_byte,
                title.byte_end,
                descriptor,
                container_record.translatable_spans,
            )
            _canonical_source_slice(text, physical)
            non_prose.append(physical)
        if token.nesting == 1:
            if token_map is not None:
                map_stack.append(token_map)
            elif inherited_map is not None:
                map_stack.append(inherited_map)
        elif token.nesting == -1 and map_stack:
            map_stack.pop()
    return ParsedMarkdownSourceMap(tuple(containers), tuple(non_prose))


def parse_markdown_with_source_map(
    text: str,
) -> tuple[Document, ParsedMarkdownSourceMap]:
    """Parse once and derive the IR and source provenance from the same tokens."""
    context = _PARSE_SOURCE.set(text)
    try:
        md = create_parser()
        tokens = md.parse(text)
        source_map = _build_parser_source_map(text, tokens)
        stream = _TokenStream(tokens)
        return _parse_document(stream), source_map
    finally:
        _PARSE_SOURCE.reset(context)


def parse_markdown(text: str) -> Document:
    """Parse markdown text into a Document IR tree."""
    return parse_markdown_with_source_map(text)[0]


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

def _parse_term_definition(stream: _TokenStream) -> TermDefinition:
    open_tok = stream.expect("term_definition_open")
    term_id = open_tok.meta.get("term_id", "")
    inline_tok = stream.expect("inline")
    stream.expect("term_definition_close")
    children = _parse_inline_children(inline_tok.children or [], inline_tok)
    return TermDefinition(term_id=term_id, children=children)

def _parse_block(stream: _TokenStream) -> BlockNode | None:
    tok = stream.peek()
    if tok is None:
        return None

    t = tok.type
    # End-of-container markers when called from nested list/blockquote parsers.
    if t in (
        "list_item_close",
        "bullet_list_close",
        "ordered_list_close",
        "blockquote_close",
    ):
        return None
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
    if t == "yfm_note_open":
        return _parse_yfm_note(stream)
    if t == "yfm_tabs_open":
        return _parse_yfm_tabs(stream) 
    if t == "yfm_include":
        return _parse_yfm_include(stream)    
    if t == "yfm_if_open":
        return _parse_yfm_if(stream)
    if t == "yfm_cut_open":
        return _parse_yfm_cut(stream)
    if t == "term_definition_open":
        return _parse_term_definition(stream)
    if t == "front_matter":
        # mdit may emit spurious empty front_matter inside nested lists (§6.80.2).
        stream.advance()
        return _parse_block(stream)

    # Unknown token — skip with a warning later. For now, advance to avoid infinite loop.
    raise ValueError(f"Unsupported block token: {t} (content={tok.content!r})")


def _parse_paragraph(stream: _TokenStream) -> Paragraph:
    stream.expect("paragraph_open")
    inline_tok = stream.expect("inline")
    stream.expect("paragraph_close")
    children = _parse_inline_children(inline_tok.children or [], inline_tok)
    return Paragraph(children=children)


def _parse_heading(stream: _TokenStream) -> Heading:
    open_tok = stream.expect("heading_open")
    level = int(open_tok.tag[1])  # h1 -> 1
    inline_tok = stream.expect("inline")
    stream.expect("heading_close")
    children = _parse_inline_children(inline_tok.children or [], inline_tok)

    # Extract YFM anchor {#anchor-id} from the trailing text.
    anchor = None
    if children and isinstance(children[-1], InlineText):
        text = children[-1].content
        # Match " {#anchor-id}" at the end.
        import re

        m = re.search(r"\s*\{#([^}]+)\}\s*$", text)
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
                children=_parse_inline_children(inline_tok.children or [], inline_tok),
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
                        children=_parse_inline_children(inline_tok.children or [], inline_tok),
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

def _parse_yfm_note(stream: _TokenStream) -> YfmNote:
    open_tok = stream.expect("yfm_note_open")
    note_type = open_tok.meta.get("note_type", "info")
    title = open_tok.meta.get("title")

    children: list[BlockNode] = []
    while True:
        tok = stream.peek()
        if tok is None or tok.type == "yfm_note_close":
            break
        block = _parse_block(stream)
        if block is not None:
            children.append(block)

    stream.expect("yfm_note_close")
    return YfmNote(note_type=note_type, title=title, children=children)

def _parse_yfm_tabs(stream: _TokenStream) -> YfmTabs:
    open_tok = stream.expect("yfm_tabs_open")
    variant = open_tok.meta.get("variant", "tabs")
    tabs: list[YfmTab] = []
    container_id = open_tok.meta["container_id"]
    while True:
        tok = stream.peek()
        if tok is None or tok.type == "yfm_tabs_close":
            break
        if tok.type != "yfm_tab_open" or tok.meta.get("container_id") != container_id:
            raise AmbiguousYfmStructureError(
                "tabs content is not enclosed by a paired direct-depth tab token"
            )
        tab_open = stream.advance()
        children: list[BlockNode] = []
        while True:
            body_token = stream.peek()
            if body_token is None or body_token.type == "yfm_tab_close":
                break
            block = _parse_block(stream)
            if block is not None:
                children.append(block)
        tab_close = stream.expect("yfm_tab_close")
        if tab_close.meta.get("container_id") != container_id:
            raise AmbiguousYfmStructureError("tabs close token belongs to another container")
        from ydbdoc_review.parsing.inline_parser import parse_inline_text

        tabs.append(
            YfmTab(
                title=parse_inline_text(tab_open.meta["title"]),
                children=children,
                source_span=SourceSpan.model_validate(tab_open.meta["source_span"]),
                title_span=SourceSpan.model_validate(tab_open.meta["title_span"]),
            )
        )
    close_tok = stream.expect("yfm_tabs_close")
    if close_tok.meta.get("container_id") != container_id:
        raise AmbiguousYfmStructureError("tabs container close token does not match opener")
    return YfmTabs(
        variant=variant,
        children=tabs,
        container_id=container_id,
        parent_container_id=open_tok.meta.get("parent_container_id"),
        opening_span=SourceSpan.model_validate(open_tok.meta["opening_span"]),
        closing_span=SourceSpan.model_validate(close_tok.meta["closing_span"]),
        opening_indent=open_tok.meta["opening_indent"],
    )

def _parse_yfm_include(stream: _TokenStream) -> YfmInclude:
    tok = stream.expect("yfm_include")
    return YfmInclude(
        text=tok.meta.get("text", ""),
        path=tok.meta.get("path", ""),
        notitle=tok.meta.get("notitle", False),
        source_span=_token_line_span(tok),
    )

def _parse_yfm_if(stream: _TokenStream) -> YfmIf:
    stream.expect("yfm_if_open")
    branches: list[YfmIfBranch] = []
    while True:
        tok = stream.peek()
        if tok is None or tok.type == "yfm_if_close":
            break
        if tok.type != "yfm_if_branch_open":
            raise ValueError(
                f"Expected yfm_if_branch_open inside yfm_if, got {tok.type}"
            )
        branches.append(_parse_yfm_if_branch(stream))
    stream.expect("yfm_if_close")
    if not branches:
        raise ValueError("yfm_if must contain at least one branch")
    return YfmIf(branches=branches)


def _parse_yfm_if_branch(stream: _TokenStream) -> YfmIfBranch:
    branch_open = stream.expect("yfm_if_branch_open")
    condition = branch_open.meta.get("condition")
    children: list[BlockNode] = []
    while True:
        tok = stream.peek()
        if tok is None or tok.type == "yfm_if_branch_close":
            break
        block = _parse_block(stream)
        if block is not None:
            children.append(block)
    stream.expect("yfm_if_branch_close")
    return YfmIfBranch(condition=condition, children=children)

def _parse_yfm_cut(stream: _TokenStream) -> YfmCut:
    open_tok = stream.expect("yfm_cut_open")
    title = open_tok.meta.get("title", "")
    children: list[BlockNode] = []
    while True:
        tok = stream.peek()
        if tok is None or tok.type == "yfm_cut_close":
            break
        block = _parse_block(stream)
        if block is not None:
            children.append(block)
    stream.expect("yfm_cut_close")
    return YfmCut(title=title, children=children)


def _list_item_to_tab(item: ListItem) -> YfmTab:
    """Convert a bullet list item into a YfmTab.

    The first child should be a Paragraph whose inline children form the tab title.
    All subsequent children form the tab body.
    """
    if not item.children:
        return YfmTab(title=[], children=[])

    first = item.children[0]
    rest = item.children[1:]

    if isinstance(first, Paragraph):
        title = first.children
        return YfmTab(title=title, children=list(rest))
    else:
        # Unusual: first block isn't a paragraph. Treat title as empty.
        return YfmTab(title=[], children=list(item.children))


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


def _span_at(start: int, end: int) -> SourceSpan | None:
    source = _PARSE_SOURCE.get()
    if source is None:
        return None
    prefix = source[:start]
    return SourceSpan(
        byte_start=len(prefix.encode("utf-8")),
        byte_end=len(source[:end].encode("utf-8")),
        line=prefix.count("\n") + 1,
        column=len(prefix.rsplit("\n", 1)[-1]) + 1,
    )


def _inline_start(token: Token) -> int | None:
    source = _PARSE_SOURCE.get()
    if source is None or token.map is None:
        return None
    line_start = 0
    for _ in range(token.map[0]):
        line_start = source.find("\n", line_start) + 1
    found = source.find(token.content, line_start)
    return found if found >= 0 else None


def _token_line_span(token: Token) -> SourceSpan | None:
    source = _PARSE_SOURCE.get()
    if source is None or token.map is None:
        return None
    start = 0
    for _ in range(token.map[0]):
        start = source.find("\n", start) + 1
    end = source.find("\n", start)
    return _span_at(start, len(source) if end < 0 else end)


def _link_ranges(raw: str) -> list[tuple[int, int]]:
    """Parser-side cursor ranges for complete inline-link syntax, never images."""
    result: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(raw):
        start = raw.find("[", cursor)
        if start < 0:
            break
        if start and raw[start - 1] in "!\\":
            cursor = start + 1
            continue
        depth = 1
        close = start + 1
        while close < len(raw) and depth:
            if raw[close] == "\\":
                close += 2
                continue
            if raw[close] == "[":
                depth += 1
            elif raw[close] == "]":
                depth -= 1
            close += 1
        if depth or close >= len(raw):
            cursor = start + 1
            continue
        if raw[close] == "(":
            end = close + 1
            parens = 1
            while end < len(raw) and parens:
                if raw[end] == "\\":
                    end += 2
                    continue
                if raw[end] == "(":
                    parens += 1
                elif raw[end] == ")":
                    parens -= 1
                end += 1
            if not parens:
                result.append((start, end))
                cursor = end
                continue
        elif raw[close] == "[":
            end = raw.find("]", close + 1)
            if end >= 0:
                result.append((start, end + 1))
                cursor = end + 1
                continue
        cursor = start + 1
    return result


def _link_spans(tokens: list[Token], source_token: Token | None) -> dict[int, SourceSpan]:
    if source_token is None:
        return {}
    start = _inline_start(source_token)
    if start is None:
        return {}
    ranges = _link_ranges(source_token.content)
    opens = [token for token in tokens if token.type == "link_open"]
    return {
        id(token): span
        for token, (left, right) in zip(opens, ranges, strict=False)
        if (span := _span_at(start + left, start + right)) is not None
    }


def _parse_inline_children(
    tokens: list[Token], source_token: Token | None = None
) -> list[InlineNode]:
    """Convert a flat token stream and parser-owned cursors into InlineNode."""
    stream = _TokenStream(tokens)
    return _parse_inline_until(stream, close_type=None, link_spans=_link_spans(tokens, source_token))


def _parse_inline_until(
    stream: _TokenStream, close_type: str | None, link_spans: dict[int, SourceSpan]
) -> list[InlineNode]:
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
            inner = _parse_inline_until(stream, "em_close", link_spans)
            stream.expect("em_close")
            marker = tok.markup if tok.markup in ("*", "_") else "*"
            children.append(InlineEmphasis(children=inner, marker=marker))  # type: ignore[arg-type]
        elif t == "strong_open":
            stream.advance()
            inner = _parse_inline_until(stream, "strong_close", link_spans)
            stream.expect("strong_close")
            marker = tok.markup if tok.markup in ("**", "__") else "**"
            children.append(InlineStrong(children=inner, marker=marker))  # type: ignore[arg-type]
        elif t == "link_open":
            stream.advance()
            href = tok.attrGet("href") or ""
            title = tok.attrGet("title")
            source_span = link_spans.get(id(tok))
            inner = _parse_inline_until(stream, "link_close", link_spans)
            stream.expect("link_close")
            children.append(InlineLink(href=href, title=title, children=inner, source_span=source_span))
        elif t == "image":
            stream.advance()
            src = tok.attrGet("src") or ""
            title = tok.attrGet("title")
            alt = tok.content
            meta = tok.meta or {}
            width = meta.get("width")
            height = meta.get("height")
            # Normalize empty strings: only one side may be missing.
            children.append(
                InlineImage(
                    src=src,
                    title=title,
                    alt=alt,
                    width=width if width else None,
                    height=height if height else None,
                )
            )
        elif t == "s_open":
            stream.advance()
            inner = _parse_inline_until(stream, "s_close", link_spans)
            stream.expect("s_close")
            children.append(InlineStrike(children=inner))
        elif t == "yfm_variable":
            stream.advance()
            children.append(
                InlineVariable(name=tok.content, raw=tok.markup)
            )
        elif t == "term_ref":
            stream.advance()
            children.append(InlineTermRef(term_id=tok.content))    
        else:
            raise ValueError(f"Unsupported inline token: {t} (content={tok.content!r})")

    return children
