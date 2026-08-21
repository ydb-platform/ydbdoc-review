"""AST → list[Segment] extractor.

Walks a Document and emits one Segment per translatable inline-bearing leaf.
Records an ``ast_path`` for each segment so translations can be written back.

The ``ast_path`` is a list of mixed int/string steps:
- ``int`` steps index into ``.children`` (for most nodes) or ``.branches``
  (for ``YfmIf``).
- ``"header"`` / ``"row"`` / ``"title"`` are typed markers used for nodes
  whose structure is not a simple list of children (tables, tabs).
"""

from __future__ import annotations

import re
from typing import Iterable

from ydbdoc_review.parsing.ast_types import (
    BlockNode,
    BlockQuote,
    BulletList,
    Document,
    Heading,
    ListItem,
    OrderedList,
    Paragraph,
    Table,
    TermDefinition,
    YfmCut,
    YfmIf,
    YfmNote,
    YfmTabs,
)
from ydbdoc_review.parsing.front_matter import translatable_front_matter_fields
from ydbdoc_review.segmentation.inline_protector import protect_inline
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.validation.homoglyphs import normalize_confusable_cyrillic


# Tab titles that are language/format identifiers — left untranslated.
DEFAULT_TAB_TITLE_WHITELIST: frozenset[str] = frozenset(
    {
        "python", "go", "java", "bash", "shell", "sh", "zsh",
        "c++", "cpp", "c#", "csharp", "node.js", "nodejs", "javascript", "js",
        "typescript", "ts", "rust", "ruby", "php", "kotlin", "swift",
        "scala", "perl", "r", "lua", "haskell", "elixir", "erlang",
        "json", "yaml", "yml", "csv", "tsv", "xml", "html", "css", "sql",
        "toml", "ini", "env",
        "linux", "macos", "windows", "docker", "kubernetes", "k8s",
        "native sdk", "native sdk (asyncio)", "database/sql", "jdbc",
    }
)

_YFM_CONTROL_FALLBACK = re.compile(
    r"^\{%\s*(?:endlist|endcut|endnote|endif|else|elsif\b[^%]*)\s*%\}$"
)


def extract_segments(
    doc: Document,
    *,
    tab_title_whitelist: frozenset[str] = DEFAULT_TAB_TITLE_WHITELIST,
) -> list[Segment]:
    """Top-level: extract all translatable segments from a document."""
    state = _ExtractState(tab_title_whitelist)
    state.extract_front_matter(doc)
    state.walk_blocks(doc.children, ast_path=[], path=[])
    return state.segments


class _ExtractState:
    def __init__(self, whitelist: frozenset[str]) -> None:
        self.segments: list[Segment] = []
        self.tab_title_whitelist = whitelist

    # -- id helpers --
    def next_id(self) -> str:
        return f"s{len(self.segments) + 1:04d}"

    def extract_front_matter(self, doc: Document) -> None:
        if doc.front_matter is None:
            return
        for key, text in translatable_front_matter_fields(doc.front_matter).items():
            self.segments.append(
                Segment(
                    id=self.next_id(),
                    kind=SegmentKind.FRONT_MATTER,
                    path=[f"front_matter:{key}"],
                    text=text,
                    placeholders=[],
                    ast_path=[key],
                )
            )

    # -- block walking --
    def walk_blocks(
        self,
        blocks: list[BlockNode],
        ast_path: list,
        path: list[str],
    ) -> None:
        for i, block in enumerate(blocks):
            self.walk_block(block, ast_path + [i], path)

    def walk_block(
        self,
        block: BlockNode,
        ast_path: list,
        path: list[str],
    ) -> None:
        if isinstance(block, Paragraph):
            # If a legacy/misaligned YFM container falls back to a paragraph,
            # its control marker is still structure, never translatable text.
            # Excluding it keeps RU/EN alignment stable after canonical render
            # (#37673 / #50729 vector-search).
            if _YFM_CONTROL_FALLBACK.match(_short_inline_preview(block.children)):
                return
            if (
                path
                and path[-1] == "list_item"
                and _is_whitelisted_tab_title(
                    _short_inline_preview(block.children), self.tab_title_whitelist
                )
            ):
                # A malformed legacy tabs container can fall back to a plain
                # Markdown list. Its exact SDK language labels are structural,
                # just like normal YfmTab titles, and must not shift alignment.
                return
            self._emit_inline_segment(
                SegmentKind.PARAGRAPH, block.children, ast_path, path
            )
        elif isinstance(block, Heading):
            self._emit_inline_segment(
                SegmentKind.HEADING,
                block.children,
                ast_path,
                path,
                heading_anchor=block.anchor,
            )
        elif isinstance(block, BulletList) or isinstance(block, OrderedList):
            for j, item in enumerate(block.children):
                self.walk_list_item(item, ast_path + [j], path)
        elif isinstance(block, BlockQuote):
            self.walk_blocks(block.children, ast_path, path + ["blockquote"])
        elif isinstance(block, Table):
            self.walk_table(block, ast_path, path)
        elif isinstance(block, YfmNote):
            # Note titles are plain strings, not inline lists — we don't
            # segment them here. Future work: add a separate pass for them.
            self.walk_blocks(
                block.children, ast_path, path + [f"note:{block.note_type}"]
            )
        elif isinstance(block, YfmTabs):
            self.walk_tabs(block, ast_path, path)
        elif isinstance(block, YfmCut):
            # Cut titles are plain strings — not segmented in B.1.
            self.walk_blocks(block.children, ast_path, path + ["cut"])
        elif isinstance(block, YfmIf):
            for k, branch in enumerate(block.branches):
                label = (
                    f"if:{branch.condition}" if branch.condition else "else"
                )
                self.walk_blocks(
                    branch.children, ast_path + [k], path + [label]
                )
        elif isinstance(block, TermDefinition):
            self._emit_inline_segment(
                SegmentKind.TERM_DEFINITION,
                block.children,
                ast_path,
                path + [f"term:{block.term_id}"],
            )
        # fenced_code, indented_code, thematic_break, html_block, yfm_include:
        # not translatable, do nothing.

    def walk_list_item(
        self,
        item: ListItem,
        ast_path: list,
        path: list[str],
    ) -> None:
        # The list item's own text is the first Paragraph child. Sub-lists,
        # code blocks etc. follow as additional blocks.
        for k, child in enumerate(item.children):
            self.walk_block(child, ast_path + [k], path + ["list_item"])

    def walk_table(
        self,
        table: Table,
        ast_path: list,
        path: list[str],
    ) -> None:
        # Header cells: ast_path + ["header", col_idx]
        for cidx, cell in enumerate(table.header.cells):
            self._emit_inline_segment(
                SegmentKind.TABLE_HEADER_CELL,
                cell.children,
                ast_path + ["header", cidx],
                path + [f"table:header:col{cidx + 1}"],
            )
        # Body cells: ast_path + ["row", row_idx, col_idx]
        for ridx, row in enumerate(table.rows):
            for cidx, cell in enumerate(row.cells):
                self._emit_inline_segment(
                    SegmentKind.TABLE_BODY_CELL,
                    cell.children,
                    ast_path + ["row", ridx, cidx],
                    path + [f"table:row{ridx + 1}:col{cidx + 1}"],
                )

    def walk_tabs(
        self,
        tabs: YfmTabs,
        ast_path: list,
        path: list[str],
    ) -> None:
        for k, tab in enumerate(tabs.children):
            tab_title_text = _short_inline_preview(tab.title)
            # Title segment: ast_path_to_yfmtabs + [tab_idx, "title"]
            if not _is_whitelisted_tab_title(
                tab_title_text, self.tab_title_whitelist
            ):
                self._emit_inline_segment(
                    SegmentKind.TAB_TITLE,
                    tab.title,
                    ast_path + [k, "title"],
                    path + [f"tab:{tab_title_text}"],
                )
            # Tab body: descend through YfmTab.children as a normal block list.
            # ast_path: ast_path_to_yfmtabs + [tab_idx]; walk_blocks adds child
            # index, so a body block at index 0 becomes [..., tab_idx, 0].
            self.walk_blocks(
                tab.children,
                ast_path + [k],
                path + [f"tab:{tab_title_text}"],
            )

    # -- emit one inline-bearing segment --
    def _emit_inline_segment(
        self,
        kind: SegmentKind,
        inline_children: list,
        ast_path: list,
        path: list[str],
        *,
        heading_anchor: str | None = None,
    ) -> None:
        if not inline_children:
            return
        text, placeholders = protect_inline(inline_children)
        if not text.strip():
            return
        self.segments.append(
            Segment(
                id=self.next_id(),
                kind=kind,
                path=list(path),
                text=text,
                placeholders=placeholders,
                ast_path=list(ast_path),
                heading_anchor=heading_anchor if kind == SegmentKind.HEADING else None,
            )
        )


# --- helpers ---


def _short_inline_preview(children: Iterable) -> str:
    """Make a path-friendly text preview from inline children."""
    out: list[str] = []
    for n in children:
        if hasattr(n, "content"):
            out.append(str(n.content))
        elif hasattr(n, "children"):
            out.append(_short_inline_preview(n.children))
    preview = "".join(out).strip()
    return preview[:60]


def _is_whitelisted_tab_title(title: str, whitelist: frozenset[str]) -> bool:
    import re

    raw = title.strip()
    normalized = normalize_confusable_cyrillic(raw).lower()
    if normalized in whitelist:
        return True
    # "Python (alternative)" / "Python (альтернативный)" — same pane as lang
    # (§6.193). Match on raw title: confusable normalize mangles Cyrillic words.
    m = re.match(
        r"^(.+?)\s*\((?:alternative|альтернативный)\)$",
        raw,
        flags=re.IGNORECASE,
    )
    if m:
        base = normalize_confusable_cyrillic(m.group(1).strip()).lower()
        if base in whitelist:
            return True
    return False
