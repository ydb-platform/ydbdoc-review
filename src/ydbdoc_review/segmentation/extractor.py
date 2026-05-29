"""AST → list[Segment] extractor.

Walks a Document and emits one Segment per translatable inline-bearing leaf.
Records an ``ast_path`` for each segment so translations can be written back.
"""

from __future__ import annotations

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
    TableCell,
    TableRow,
    TermDefinition,
    YfmCut,
    YfmIf,
    YfmIfBranch,
    YfmNote,
    YfmTab,
    YfmTabs,
)
from ydbdoc_review.segmentation.inline_protector import protect_inline
from ydbdoc_review.segmentation.types import Segment, SegmentKind


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
    }
)


def extract_segments(
    doc: Document,
    *,
    tab_title_whitelist: frozenset[str] = DEFAULT_TAB_TITLE_WHITELIST,
) -> list[Segment]:
    """Top-level: extract all translatable segments from a document."""
    state = _ExtractState(tab_title_whitelist)
    state.walk_blocks(doc.children, ast_path=[], path=[])
    return state.segments


class _ExtractState:
    def __init__(self, whitelist: frozenset[str]) -> None:
        self.segments: list[Segment] = []
        self.tab_title_whitelist = whitelist

    # -- id helpers --
    def next_id(self) -> str:
        return f"s{len(self.segments) + 1:04d}"

    # -- block walking --
    def walk_blocks(
        self, blocks: list[BlockNode], ast_path: list[int], path: list[str]
    ) -> None:
        for i, block in enumerate(blocks):
            self.walk_block(block, ast_path + [i], path)

    def walk_block(
        self, block: BlockNode, ast_path: list[int], path: list[str]
    ) -> None:
        if isinstance(block, Paragraph):
            self._emit_inline_segment(
                SegmentKind.PARAGRAPH, block.children, ast_path, path
            )
        elif isinstance(block, Heading):
            heading_text_preview = _short_inline_preview(block.children)
            self._emit_inline_segment(
                SegmentKind.HEADING, block.children, ast_path, path
            )
            # Descend? No — heading itself has only inline children. The
            # heading text becomes the new path component for following siblings.
            # Mutate `path` is the caller's responsibility — we handle this in
            # walk_blocks by tracking last heading per level. For simplicity in
            # the first iteration we don't track section nesting; the segment's
            # `path` is taken at extraction time. Future improvement.
            _ = heading_text_preview
        elif isinstance(block, BulletList) or isinstance(block, OrderedList):
            for j, item in enumerate(block.children):
                self.walk_list_item(item, ast_path + [j], path)
        elif isinstance(block, BlockQuote):
            self.walk_blocks(block.children, ast_path, path + ["blockquote"])
        elif isinstance(block, Table):
            self.walk_table(block, ast_path, path)
        elif isinstance(block, YfmNote):
            sub_path = path + [f"note:{block.note_type}"]
            # The title is not a translatable inline list in our model — it's a
            # plain string. We do NOT segment it here; if the LLM should
            # translate it later, we'll handle it in a separate pass.
            self.walk_blocks(block.children, ast_path, sub_path)
        elif isinstance(block, YfmTabs):
            self.walk_tabs(block, ast_path, path)
        elif isinstance(block, YfmCut):
            # Title is plain string; not segmented here.
            self.walk_blocks(block.children, ast_path, path + ["cut"])
        elif isinstance(block, YfmIf):
            for k, branch in enumerate(block.branches):
                label = (
                    f"if:{branch.condition}" if branch.condition else "else"
                )
                self.walk_blocks(branch.children, ast_path + [k], path + [label])
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
        self, item: ListItem, ast_path: list[int], path: list[str]
    ) -> None:
        # In tight lists, the immediate Paragraph children represent the list
        # item's own text. Nested blocks (sub-lists, code, notes) come after.
        for k, child in enumerate(item.children):
            self.walk_block(child, ast_path + [k], path + ["list_item"])

    def walk_table(
        self, table: Table, ast_path: list[int], path: list[str]
    ) -> None:
        # Header row.
        for cidx, cell in enumerate(table.header.cells):
            self._emit_inline_segment(
                SegmentKind.TABLE_HEADER_CELL,
                cell.children,
                ast_path + [0, cidx],  # 0 = header
                path + [f"table:header:col{cidx + 1}"],
            )
        for ridx, row in enumerate(table.rows):
            for cidx, cell in enumerate(row.cells):
                self._emit_inline_segment(
                    SegmentKind.TABLE_BODY_CELL,
                    cell.children,
                    ast_path + [1 + ridx, cidx],  # 1+ = body rows
                    path + [f"table:row{ridx + 1}:col{cidx + 1}"],
                )

    def walk_tabs(
        self, tabs: YfmTabs, ast_path: list[int], path: list[str]
    ) -> None:
        for k, tab in enumerate(tabs.children):
            tab_title_text = _short_inline_preview(tab.title)
            if not _is_whitelisted_tab_title(
                tab_title_text, self.tab_title_whitelist
            ):
                self._emit_inline_segment(
                    SegmentKind.TAB_TITLE,
                    tab.title,
                    ast_path + [k, 0],  # 0 = title
                    path + [f"tab:{tab_title_text}"],
                )
            self.walk_blocks(
                tab.children,
                ast_path + [k, 1],  # 1 = children
                path + [f"tab:{tab_title_text}"],
            )

    # -- emit one inline-bearing segment --
    def _emit_inline_segment(
        self,
        kind: SegmentKind,
        inline_children: list,
        ast_path: list[int],
        path: list[str],
    ) -> None:
        if not inline_children:
            return  # nothing to translate
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
    return title.strip().lower() in whitelist

