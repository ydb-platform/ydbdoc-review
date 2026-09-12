"""Additional reinsert tests for error paths and segment kinds."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ydbdoc_review.parsing.ast_types import InlineText
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.reinsert import (
    ReinsertError,
    _navigate_to_doc_index,
    _set_inline_at_ast_path,
    _split_text_by_placeholders,
    reinsert_segments,
)
from ydbdoc_review.segmentation.types import Segment, SegmentKind


def test_split_text_empty_without_placeholders():
    assert _split_text_by_placeholders("", {}) == []
    assert _split_text_by_placeholders("plain", {}) == [InlineText(content="plain")]


def test_reinsert_keeps_original_when_translation_missing():
    doc = parse_markdown("Hello world.\n")
    segments = extract_segments(doc)
    out = render_markdown(reinsert_segments(doc, segments, {}))
    assert "Hello world." in out


def test_translate_table_header_cell():
    text = "| Name | Value |\n| --- | --- |\n| a | 1 |\n"
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    header = next(s for s in segments if s.kind == SegmentKind.TABLE_HEADER_CELL)
    out = render_markdown(
        reinsert_segments(doc, segments, {header.id: "Имя"})
    )
    assert "| Имя |" in out


def test_translate_tab_title():
    text = (
        "{% list tabs %}\n\n"
        "- Из консоли\n\n"
        "  Текст.\n\n"
        "{% endlist %}\n"
    )
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    title = next(s for s in segments if s.kind == SegmentKind.TAB_TITLE)
    out = render_markdown(
        reinsert_segments(doc, segments, {title.id: "From console"})
    )
    assert "From console" in out


def test_translate_term_definition():
    text = "[*term]: Definition text.\n"
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    td = next(s for s in segments if s.kind == SegmentKind.TERM_DEFINITION)
    out = render_markdown(
        reinsert_segments(doc, segments, {td.id: "Определение."})
    )
    assert "[*term]: Определение." in out


def test_reinsert_list_item_kind():
    text = "- list item text\n"
    doc = parse_markdown(text)
    base = extract_segments(doc)[0]
    seg = Segment(
        id=base.id,
        kind=SegmentKind.LIST_ITEM,
        path=base.path,
        text=base.text,
        placeholders=base.placeholders,
        ast_path=[0, 0],
    )
    out = render_markdown(reinsert_segments(doc, [seg], {seg.id: "элемент"}))
    assert "элемент" in out


def test_reinsert_blockquote_paragraph_kind():
    text = "> quoted line\n"
    doc = parse_markdown(text)
    base = extract_segments(doc)[0]
    seg = Segment(
        id=base.id,
        kind=SegmentKind.BLOCKQUOTE_PARAGRAPH,
        path=base.path,
        text=base.text,
        placeholders=base.placeholders,
        ast_path=[0, 0],
    )
    out = render_markdown(reinsert_segments(doc, [seg], {seg.id: "цитата"}))
    assert "> цитата" in out


def test_reinsert_wrong_node_type_raises():
    doc = parse_markdown("# Heading\n")
    seg = Segment(
        id="s0001",
        kind=SegmentKind.PARAGRAPH,
        path=[],
        text="x",
        placeholders=[],
        ast_path=[0],
    )
    with pytest.raises(ReinsertError, match="Expected Paragraph"):
        reinsert_segments(doc, [seg], {seg.id: "y"})


def test_reinsert_bad_table_header_path_raises():
    doc = parse_markdown("| A | B |\n| --- | --- |\n| 1 | 2 |\n")
    seg = Segment(
        id="s0001",
        kind=SegmentKind.TABLE_HEADER_CELL,
        path=[],
        text="A",
        placeholders=[],
        ast_path=[0, "header", "bad"],
    )
    with pytest.raises(ReinsertError, match="Bad col index"):
        reinsert_segments(doc, [seg], {seg.id: "X"})


def test_reinsert_bad_table_body_path_raises():
    doc = parse_markdown("| A | B |\n| --- | --- |\n| 1 | 2 |\n")
    seg = Segment(
        id="s0001",
        kind=SegmentKind.TABLE_BODY_CELL,
        path=[],
        text="1",
        placeholders=[],
        ast_path=[0, "row", "bad", 0],
    )
    with pytest.raises(ReinsertError, match="Bad row/col"):
        reinsert_segments(doc, [seg], {seg.id: "X"})


def test_reinsert_list_item_without_paragraph_raises():
    doc = parse_markdown("- item\n")
    # Point at BulletList instead of ListItem.
    seg = Segment(
        id="s0001",
        kind=SegmentKind.LIST_ITEM,
        path=[],
        text="item",
        placeholders=[],
        ast_path=[0],
    )
    with pytest.raises(ReinsertError, match="Expected ListItem"):
        reinsert_segments(doc, [seg], {seg.id: "x"})


def test_navigate_non_int_step_raises():
    doc = parse_markdown("Text.\n")
    with pytest.raises(ReinsertError, match="non-int step"):
        _navigate_to_doc_index(doc, [0, "header"])


def test_navigate_cannot_descend_raises():
    doc = parse_markdown("```\ncode\n```\n")
    with pytest.raises(ReinsertError, match="Cannot descend"):
        _navigate_to_doc_index(doc, [0, 0])


def test_unsupported_segment_kind_raises():
    doc = parse_markdown("Text.\n")
    fake = SimpleNamespace(kind=SimpleNamespace(value="unknown"), ast_path=[0])
    with pytest.raises(ReinsertError, match="Unsupported segment kind"):
        _set_inline_at_ast_path(doc, fake, [InlineText(content="x")])
