"""IR (intermediate representation) for parsed Markdown documents.

We don't use markdown-it-py's SyntaxTreeNode directly because we need:
1. Full control over serialization (round-trip).
2. Extensibility for YFM constructs (notes, cuts, tabs).
3. Stable identity for segments (for translation pipeline).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# --- Inline nodes ---


class InlineText(BaseModel):
    """Plain text inside a paragraph/heading/list-item."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["text"] = "text"
    content: str


class InlineCode(BaseModel):
    """Inline code: `code`."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["code"] = "code"
    content: str
    # marker length (number of backticks). Usually 1, but can be 2+ if content has backticks.
    marker_len: int = 1


class InlineEmphasis(BaseModel):
    """*italic* or _italic_."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["em"] = "em"
    children: list["InlineNode"]
    marker: Literal["*", "_"] = "*"


class InlineStrong(BaseModel):
    """**bold** or __bold__."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["strong"] = "strong"
    children: list["InlineNode"]
    marker: Literal["**", "__"] = "**"


class InlineLink(BaseModel):
    """[text](url "title")."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["link"] = "link"
    href: str
    title: str | None = None
    children: list["InlineNode"]


class InlineImage(BaseModel):
    """![alt](src "title")."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["image"] = "image"
    src: str
    title: str | None = None
    alt: str = ""


class InlineHTML(BaseModel):
    """Raw HTML inline: <br/>, <span>, etc."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["html_inline"] = "html_inline"
    content: str


class InlineSoftBreak(BaseModel):
    """Newline inside a paragraph (rendered as space or <br>)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["softbreak"] = "softbreak"


class InlineHardBreak(BaseModel):
    """Two trailing spaces + newline, or backslash + newline."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["hardbreak"] = "hardbreak"

class InlineVariable(BaseModel):
    """YFM variable: {{ var-name }}."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["yfm_variable"] = "yfm_variable"
    name: str
    # Original whitespace inside braces, for exact round-trip.
    # E.g. "{{ name }}" vs "{{name}}" vs "{{  name  }}".
    raw: str



InlineNode = Annotated[
    Union[
        InlineText,
        InlineCode,
        InlineEmphasis,
        InlineStrong,
        InlineLink,
        InlineImage,
        InlineHTML,
        InlineSoftBreak,
        InlineHardBreak,
        InlineVariable,
    ],
    Field(discriminator="kind"),
]



# --- Block nodes ---


class Paragraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["paragraph"] = "paragraph"
    children: list[InlineNode]


class Heading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["heading"] = "heading"
    level: int  # 1-6
    children: list[InlineNode]
    # YFM anchor: {#anchor-id} at the end of heading. Stored separately.
    anchor: str | None = None


class FencedCode(BaseModel):
    """```lang\ncontent\n```"""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["fenced_code"] = "fenced_code"
    info: str = ""  # language tag
    content: str
    fence_char: Literal["`", "~"] = "`"
    fence_len: int = 3


class IndentedCode(BaseModel):
    """4-space-indented code block."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["indented_code"] = "indented_code"
    content: str


class ThematicBreak(BaseModel):
    """--- or *** or ___"""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["thematic_break"] = "thematic_break"
    marker: str = "---"


class BlockQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["blockquote"] = "blockquote"
    children: list["BlockNode"]


class ListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["list_item"] = "list_item"
    children: list["BlockNode"]
    # For ordered lists: number prefix, e.g. "1", "2".
    marker: str = "-"


class BulletList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["bullet_list"] = "bullet_list"
    children: list[ListItem]
    marker: Literal["-", "*", "+"] = "-"
    tight: bool = True


class OrderedList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ordered_list"] = "ordered_list"
    children: list[ListItem]
    start: int = 1
    delimiter: Literal[".", ")"] = "."
    tight: bool = True


class HTMLBlock(BaseModel):
    """Block-level raw HTML."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["html_block"] = "html_block"
    content: str


# --- Tables (markdown-it GFM tables) ---


class TableCell(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["table_cell"] = "table_cell"
    children: list[InlineNode]
    is_header: bool = False
    align: Literal["left", "center", "right", "none"] = "none"


class TableRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["table_row"] = "table_row"
    cells: list[TableCell]


class Table(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["table"] = "table"
    header: TableRow
    rows: list[TableRow]
    aligns: list[Literal["left", "center", "right", "none"]] = []

class YfmNote(BaseModel):
    """YFM note container: {% note TYPE %} ... {% endnote %}."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["yfm_note"] = "yfm_note"
    note_type: str  # info, tip, warning, alert, important
    # Optional title in quotes: {% note info "Custom Title" %}
    title: str | None = None
    children: list["BlockNode"]

class YfmTab(BaseModel):
    """A single tab inside a {% list tabs %} container."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["yfm_tab"] = "yfm_tab"
    # Tab title is inline content (usually plain text like "Python", "Go",
    # but can contain code, links, variables).
    title: list["InlineNode"]
    children: list["BlockNode"]


class YfmTabs(BaseModel):
    """YFM tabs container: {% list tabs %} ... {% endlist %}."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["yfm_tabs"] = "yfm_tabs"
    # Variant: "tabs" or "tabs accordion" or "tabs radio" etc.
    variant: str = "tabs"
    children: list[YfmTab]



BlockNode = Annotated[
    Union[
        Paragraph,
        Heading,
        FencedCode,
        IndentedCode,
        ThematicBreak,
        BlockQuote,
        BulletList,
        OrderedList,
        HTMLBlock,
        Table,
        YfmNote,
        YfmTabs,
    ],
    Field(discriminator="kind"),
]



# --- Document root ---


class Document(BaseModel):
    """Root node of a parsed markdown document."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["document"] = "document"
    children: list[BlockNode]
    # Optional YAML front matter.
    front_matter: str | None = None


# Resolve forward references
BlockQuote.model_rebuild()
ListItem.model_rebuild()
InlineEmphasis.model_rebuild()
InlineStrong.model_rebuild()
InlineLink.model_rebuild()
YfmNote.model_rebuild()
YfmTab.model_rebuild()
YfmTabs.model_rebuild()
