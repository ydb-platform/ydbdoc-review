"""Production Markdown/YFM token grammar and source-located review blocks."""

from __future__ import annotations

from dataclasses import dataclass, fields

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.front_matter import front_matter_plugin

from ydbdoc_review.parsing.inline_locations import SourceSpan, install_inline_location_tracking
from ydbdoc_review.parsing.yfm_plugins.conditionals import yfm_if_plugin
from ydbdoc_review.parsing.yfm_plugins.cuts import yfm_cut_plugin
from ydbdoc_review.parsing.yfm_plugins.image_size import yfm_image_size_plugin
from ydbdoc_review.parsing.yfm_plugins.includes import yfm_include_plugin
from ydbdoc_review.parsing.yfm_plugins.link_with_variable import (
    yfm_link_with_variable_plugin,
)
from ydbdoc_review.parsing.yfm_plugins.notes import yfm_note_plugin
from ydbdoc_review.parsing.yfm_plugins.tables import yfm_table_plugin
from ydbdoc_review.parsing.yfm_plugins.tabs import yfm_tabs_plugin
from ydbdoc_review.parsing.yfm_plugins.terms import yfm_terms_plugin
from ydbdoc_review.parsing.yfm_plugins.variables import yfm_variable_plugin


def create_parser(*, source_locations: bool = False) -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True, "breaks": False, "linkify": False})
    md.enable("table")
    md.enable("strikethrough")
    md.use(front_matter_plugin)
    md.use(
        yfm_link_with_variable_plugin,
        source_preserving=source_locations,
    )  # must be early (mutates source)
    md.use(yfm_variable_plugin)
    md.use(yfm_note_plugin)
    md.use(yfm_table_plugin)
    md.use(yfm_tabs_plugin)
    md.use(yfm_include_plugin)
    md.use(yfm_if_plugin)
    md.use(yfm_cut_plugin)
    md.use(yfm_terms_plugin)
    md.use(yfm_image_size_plugin)
    if source_locations:
        install_inline_location_tracking(md)
    return md


@dataclass(frozen=True)
class StructureCounts:
    headings: int = 0
    paragraphs: int = 0
    list_items: int = 0
    tables: int = 0
    rows: int = 0
    code: int = 0
    yfm: int = 0

    def __add__(self, other: StructureCounts) -> StructureCounts:
        return StructureCounts(**{f.name: getattr(self, f.name) + getattr(other, f.name)
                                  for f in fields(self)})


def _review_counts(tokens: list[Token]) -> StructureCounts:
    kinds = [token.type for token in tokens]
    return StructureCounts(
        headings=kinds.count("heading_open"), paragraphs=kinds.count("paragraph_open"),
        list_items=kinds.count("list_item_open"), tables=kinds.count("table_open"),
        rows=kinds.count("tr_open"), code=kinds.count("fence") + kinds.count("code_block"),
        yfm=sum(kind.startswith("yfm_") and kind.endswith("_open") for kind in kinds),
    )


@dataclass(frozen=True)
class LocatedReviewBlock:
    """An indivisible root container, including every nested source character."""

    kind: str
    address: tuple[int, ...]
    span: SourceSpan
    line_start: int
    line_end: int
    structure: tuple[str, ...]
    heading_level: int = 0
    anchor: str | None = None
    counts: StructureCounts = StructureCounts()


def parse_review_blocks(text: str) -> tuple[LocatedReviewBlock, ...]:
    """Use the configured parser's root maps, never AST rendering or text search.

    Nested nodes belong to their complete root container. Full tables include
    cells the AST cannot represent. The caller independently checks source gaps.
    """
    tokens = create_parser(source_locations=True).parse(text)
    offsets = [0]
    # markdown-it normalizes CRLF and CR, but its maps still count source lines.
    import re
    offsets.extend(match.end() for match in re.finditer(r"\r\n|\r|\n", text))
    if offsets[-1] != len(text):
        offsets.append(len(text))
    blocks = []
    for index, token in enumerate(tokens):
        if token.level != 0 or token.nesting == -1:
            continue
        if token.map is None:
            raise ValueError("root block has no source map")
        start, end = token.map
        if not 0 <= start < end < len(offsets):
            raise ValueError("root block has invalid source map")
        end_index = index + 1
        if token.nesting == 1:
            while end_index < len(tokens):
                if tokens[end_index].level == 0 and tokens[end_index].nesting == -1:
                    end_index += 1
                    break
                end_index += 1
        structure = tuple(
            repr((nested.type, nested.tag, nested.info if nested.type == "fence" else "",
                  tuple((key, nested.meta[key]) for key in ("note_type", "condition", "branch_kind", "term_id")
                        if key in nested.meta)))
            for nested in tokens[index:end_index] if nested.type != "inline"
        )
        anchor = re.search(r"\{#([^}]+)\}\s*$", text[offsets[start]:offsets[end]]) if token.type == "heading_open" else None
        blocks.append(LocatedReviewBlock(
            token.type.removesuffix("_open"), (len(blocks),),
            SourceSpan(offsets[start], offsets[end]), start + 1, end,
            structure, int(token.tag[1:]) if token.type == "heading_open" else 0,
            anchor.group(1) if anchor else None,
            _review_counts(tokens[index:end_index]),
        ))
    return tuple(blocks)


# --- Inline parsing ---
