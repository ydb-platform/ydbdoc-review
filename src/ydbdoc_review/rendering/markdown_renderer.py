"""IR → Markdown renderer.

Designed for stable round-trip: render(parse(render(parse(x)))) == render(parse(x)).
"""

from __future__ import annotations

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
    ListItem,
    OrderedList,
    Paragraph,
    Table,
    TableCell,
    TableRow,
    ThematicBreak,
    InlineTermRef,    
    InlineVariable,  
    TermDefinition,  
    YfmCut,
    YfmIf,           
    YfmIfBranch,     # noqa: F401 — used by isinstance only
    YfmInclude,
    YfmNote,
    YfmTab,    
    YfmTabs,   
)


def render_markdown(doc: Document) -> str:
    """Render a Document back to markdown text."""
    parts: list[str] = []
    if doc.front_matter is not None:
        parts.append(f"---\n{doc.front_matter}---\n")
    for i, block in enumerate(doc.children):
        if i > 0:
            parts.append("\n")
        parts.append(_render_block(block, indent=""))
    out = "".join(parts)
    if not out.endswith("\n"):
        out += "\n"
    return out

def _render_yfm_note(n: YfmNote, indent: str) -> str:
    title_part = f' "{n.title}"' if n.title else ""
    open_line = f"{indent}{{% note {n.note_type}{title_part} %}}\n"
    close_line = f"{indent}{{% endnote %}}\n"

    inner_parts: list[str] = []
    for i, child in enumerate(n.children):
        if i > 0:
            inner_parts.append("\n")
        inner_parts.append(_render_block(child, indent=""))
    inner = "".join(inner_parts)

    # YFM notes need a blank line after open tag and before close tag.
    return f"{open_line}\n{inner}\n{close_line}"
def _render_yfm_tabs(t: YfmTabs, indent: str) -> str:
    open_line = f"{indent}{{% {t.variant} %}}\n"
    # Diplodoc syntax: {% list tabs %} — variant already starts with "tabs",
    # but the opening tag is "{% list <variant> %}".
    open_line = f"{indent}{{% list {t.variant} %}}\n"
    close_line = f"{indent}{{% endlist %}}\n"

    if not t.children:
        return f"{open_line}\n{close_line}"

    parts: list[str] = [open_line, "\n"]
    for i, tab in enumerate(t.children):
        if i > 0:
            parts.append("\n")
        parts.append(_render_yfm_tab(tab, indent))
    parts.append("\n")
    parts.append(close_line)
    return "".join(parts)


def _render_yfm_tab(tab: YfmTab, indent: str) -> str:
    title_text = _render_inline(tab.title)
    marker = "- "
    cont_indent = "  "

    if not tab.children:
        return f"{indent}{marker}{title_text}\n"

    # Render body blocks.
    body_parts: list[str] = []
    for i, child in enumerate(tab.children):
        if i > 0:
            body_parts.append("\n")
        body_parts.append(_render_block(child, indent=""))
    body = "".join(body_parts).rstrip("\n")

    body_lines = body.split("\n")
    out_lines = [f"{indent}{marker}{title_text}", ""]
    for line in body_lines:
        if line == "":
            out_lines.append("")
        else:
            out_lines.append(f"{indent}{cont_indent}{line}")
    return "\n".join(out_lines) + "\n"

def _render_yfm_include(i: YfmInclude, indent: str) -> str:
    notitle_part = "notitle " if i.notitle else ""
    return f"{indent}{{% include {notitle_part}[{i.text}]({i.path}) %}}\n"

def _render_yfm_if(node: YfmIf, indent: str) -> str:
    parts: list[str] = []
    for i, branch in enumerate(node.branches):
        if i == 0:
            tag = f"{{% if {branch.condition} %}}"
        elif branch.condition is not None:
            tag = f"{{% elsif {branch.condition} %}}"
        else:
            tag = "{% else %}"
        parts.append(f"{indent}{tag}\n\n")

        body_parts: list[str] = []
        for j, child in enumerate(branch.children):
            if j > 0:
                body_parts.append("\n")
            body_parts.append(_render_block(child, indent=""))
        body = "".join(body_parts)
        if body and not body.endswith("\n"):
            body += "\n"
        parts.append(body)
        parts.append("\n")

    parts.append(f"{indent}{{% endif %}}\n")
    return "".join(parts)

def _render_yfm_cut(c: YfmCut, indent: str) -> str:
    open_line = f'{indent}{{% cut "{c.title}" %}}\n'
    close_line = f"{indent}{{% endcut %}}\n"

    inner_parts: list[str] = []
    for i, child in enumerate(c.children):
        if i > 0:
            inner_parts.append("\n")
        inner_parts.append(_render_block(child, indent=""))
    inner = "".join(inner_parts)
    if inner and not inner.endswith("\n"):
        inner += "\n"

    return f"{open_line}\n{inner}\n{close_line}"

def _render_term_definition(td: TermDefinition, indent: str) -> str:
    text = _render_inline(td.children)
    return f"{indent}[*{td.term_id}]: {text}\n"

def _render_block(block: BlockNode, indent: str) -> str:
    kind = block.kind
    if kind == "paragraph":
        return _render_paragraph(block, indent)
    if kind == "heading":
        return _render_heading(block, indent)
    if kind == "fenced_code":
        return _render_fenced_code(block, indent)
    if kind == "indented_code":
        return _render_indented_code(block, indent)
    if kind == "thematic_break":
        return _render_thematic_break(block, indent)
    if kind == "blockquote":
        return _render_blockquote(block, indent)
    if kind == "bullet_list":
        return _render_bullet_list(block, indent)
    if kind == "ordered_list":
        return _render_ordered_list(block, indent)
    if kind == "html_block":
        return _render_html_block(block, indent)
    if kind == "table":
        return _render_table(block, indent)
    if kind == "yfm_note":
        return _render_yfm_note(block, indent)
    if kind == "yfm_tabs":
        return _render_yfm_tabs(block, indent)    
    if kind == "yfm_include":
        return _render_yfm_include(block, indent)
    if kind == "yfm_if":
        return _render_yfm_if(block, indent)
    if kind == "yfm_cut":
        return _render_yfm_cut(block, indent)
    if kind == "term_definition":
        return _render_term_definition(block, indent)    
    raise ValueError(f"Unknown block kind: {kind}")


def _render_paragraph(p: Paragraph, indent: str) -> str:
    text = _render_inline(p.children)
    return _prefix_lines(text, indent) + "\n"


def _render_heading(h: Heading, indent: str) -> str:
    prefix = "#" * h.level
    text = _render_inline(h.children)
    anchor = f" {{#{h.anchor}}}" if h.anchor else ""
    return f"{indent}{prefix} {text}{anchor}\n"


def _render_fenced_code(f: FencedCode, indent: str) -> str:
    fence = f.fence_char * f.fence_len
    info = f.info or ""
    content = f.content
    # markdown-it preserves a trailing newline in content. Ensure exactly one.
    if not content.endswith("\n"):
        content += "\n"
    lines = [f"{indent}{fence}{info}"]
    for line in content.split("\n")[:-1]:  # drop final empty after split
        lines.append(f"{indent}{line}")
    lines.append(f"{indent}{fence}")
    return "\n".join(lines) + "\n"


def _render_indented_code(c: IndentedCode, indent: str) -> str:
    content = c.content
    if not content.endswith("\n"):
        content += "\n"
    out_lines = []
    for line in content.split("\n")[:-1]:
        out_lines.append(f"{indent}    {line}" if line else "")
    return "\n".join(out_lines) + "\n"


def _render_thematic_break(t: ThematicBreak, indent: str) -> str:
    marker = t.marker if t.marker else "---"
    # markdown-it returns the raw marker characters (e.g. "***", "---", "___"),
    # possibly with spaces. Normalize to canonical 3-char form using the first char.
    if not marker:
        marker = "---"
    char = marker[0]
    if char not in ("-", "*", "_"):
        char = "-"
    marker = char * 3
    return f"{indent}{marker}\n"


def _render_blockquote(b: BlockQuote, indent: str) -> str:
    inner_parts: list[str] = []
    for i, child in enumerate(b.children):
        if i > 0:
            inner_parts.append("\n")
        inner_parts.append(_render_block(child, indent=""))
    inner = "".join(inner_parts)
    # Prefix each line with "> ".
    out_lines = []
    for line in inner.split("\n"):
        if line == "":
            out_lines.append(f"{indent}>")
        else:
            out_lines.append(f"{indent}> {line}")
    # Strip the trailing empty line we just added.
    if out_lines and out_lines[-1].rstrip() in (">", ""):
        out_lines.pop()
    return "\n".join(out_lines) + "\n"


def _render_bullet_list(lst: BulletList, indent: str) -> str:
    parts: list[str] = []
    for i, item in enumerate(lst.children):
        if i > 0 and not lst.tight:
            parts.append("\n")
        parts.append(_render_list_item(item, indent, marker=lst.marker, ordered=False))
    return "".join(parts)


def _render_ordered_list(lst: OrderedList, indent: str) -> str:
    parts: list[str] = []
    for i, item in enumerate(lst.children):
        if i > 0 and not lst.tight:
            parts.append("\n")
        number = lst.start + i
        marker = f"{number}{lst.delimiter}"
        parts.append(_render_list_item(item, indent, marker=marker, ordered=True))
    return "".join(parts)


def _render_list_item(
    item: ListItem, indent: str, marker: str, ordered: bool
) -> str:
    # Build inner content with proper indentation for continuation lines.
    inner_parts: list[str] = []
    for i, child in enumerate(item.children):
        if i > 0:
            inner_parts.append("\n")
        inner_parts.append(_render_block(child, indent=""))
    inner = "".join(inner_parts).rstrip("\n")

    cont_indent = " " * (len(marker) + 1)
    inner_lines = inner.split("\n")
    out_lines = [f"{indent}{marker} {inner_lines[0]}"]
    for line in inner_lines[1:]:
        if line == "":
            out_lines.append("")
        else:
            out_lines.append(f"{indent}{cont_indent}{line}")
    return "\n".join(out_lines) + "\n"


def _render_html_block(h: HTMLBlock, indent: str) -> str:
    content = h.content
    if not content.endswith("\n"):
        content += "\n"
    return _prefix_lines(content.rstrip("\n"), indent) + "\n"


def _render_table(t: Table, indent: str) -> str:
    # Compute the actual column count: max of (header cells, any row cells).
    header_cells = [
        _escape_table_cell(_render_inline(c.children)) for c in t.header.cells
    ]
    body_rows: list[list[str]] = []
    for row in t.rows:
        cells = [_escape_table_cell(_render_inline(c.children)) for c in row.cells]
        body_rows.append(cells)

    n_cols = max(
        [len(header_cells)] + [len(r) for r in body_rows],
        default=1,
    )

    while len(header_cells) < n_cols:
        header_cells.append("")
    for r in body_rows:
        while len(r) < n_cols:
            r.append("")

    seps: list[str] = []
    for align in (t.aligns + ["none"] * n_cols)[:n_cols]:
        if align == "left":
            seps.append(":---")
        elif align == "right":
            seps.append("---:")
        elif align == "center":
            seps.append(":---:")
        else:
            seps.append("---")

    lines: list[str] = []
    lines.append(f"{indent}| " + " | ".join(header_cells) + " |")
    lines.append(f"{indent}| " + " | ".join(seps) + " |")
    for row in body_rows:
        lines.append(f"{indent}| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"

def _escape_table_cell(text: str) -> str:
    """Escape literal pipes inside a rendered table cell.

    Inside a markdown table row, a literal '|' must be written as '\\|',
    otherwise it would be parsed as a column separator. We do this AFTER
    rendering inline children to a string, because some inline constructs
    (code, link href) may legitimately contain '|'.
    """
    # Replace standalone backslash-pipe sequence carefully: we don't want
    # to double-escape something already escaped. Strategy: replace '|'
    # with '\\|' only when it is NOT already preceded by an odd number of
    # backslashes. Simpler & robust: just replace '|' → '\\|', because our
    # renderer never produces '\\|' on its own (inline code preserves the
    # raw '|' character, and escaping it doesn't change its visible meaning).
    return text.replace("\\", "\\\\").replace("|", "\\|")


# --- Inline rendering ---


def _render_inline(nodes: list[InlineNode]) -> str:
    return "".join(_render_inline_node(n) for n in nodes)


def _render_inline_node(n: InlineNode) -> str:
    if isinstance(n, InlineVariable):
        return n.raw
    if isinstance(n, InlineText):
        return n.content
    if isinstance(n, InlineCode):
        marker = "`" * n.marker_len
        # If content contains the marker, we need to pad with spaces.
        content = n.content
        if marker in content:
            return f"{marker} {content} {marker}"
        return f"{marker}{content}{marker}"
    if isinstance(n, InlineEmphasis):
        inner = _render_inline(n.children)
        return f"{n.marker}{inner}{n.marker}"
    if isinstance(n, InlineStrong):
        inner = _render_inline(n.children)
        return f"{n.marker}{inner}{n.marker}"
    if isinstance(n, InlineLink):
        inner = _render_inline(n.children)
        title = f' "{n.title}"' if n.title else ""
        return f"[{inner}]({n.href}{title})"
    if isinstance(n, InlineImage):
        title = f' "{n.title}"' if n.title else ""
        size = ""
        if n.width is not None or n.height is not None:
            w = n.width or ""
            h = n.height or ""
            size = f" ={w}x{h}"
        return f"![{n.alt}]({n.src}{size}{title})"
    if isinstance(n, InlineHTML):
        return n.content
    if isinstance(n, InlineSoftBreak):
        return "\n"
    if isinstance(n, InlineHardBreak):
        return "  \n"
    if isinstance(n, InlineTermRef):
        return f"[*{n.term_id}]"
    
    raise ValueError(f"Unknown inline node: {type(n).__name__}")


def _prefix_lines(text: str, indent: str) -> str:
    if not indent:
        return text
    lines = text.split("\n")
    return "\n".join((indent + line) if line else line for line in lines)
