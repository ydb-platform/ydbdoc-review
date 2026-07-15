"""Tests for YFM heading anchor helpers."""

from __future__ import annotations

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.reinsert import reinsert_segments
from ydbdoc_review.segmentation.types import SegmentKind
from ydbdoc_review.validation.link_locale import localize_links_in_document
from ydbdoc_review.validation.yfm_anchor import (
    build_heading_anchor_map,
    diplodoc_auto_slug,
    english_yfm_anchor,
)


def test_english_yfm_anchor_translates_cyrillic_suffix():
    assert (
        english_yfm_anchor("fields-Описание", "Description of fields in the response")
        == "fields-Description"
    )


def test_english_yfm_anchor_keeps_ascii():
    assert english_yfm_anchor("examples", "Examples") == "examples"


def test_diplodoc_auto_slug_cyrillic_and_latin():
    assert diplodoc_auto_slug("Векторный поиск") == "векторный-поиск"
    assert diplodoc_auto_slug("Vector search") == "vector-search"


def test_cyrillic_anchor_parsed_and_rendered_in_english():
    text = "### Описание полей в ответе {#fields-Описание}\n\nBody.\n"
    doc = parse_markdown(text)
    assert doc.children[0].anchor == "fields-Описание"
    segments = extract_segments(doc)
    heading_seg = next(s for s in segments if s.kind == SegmentKind.HEADING)
    assert "{#" not in heading_seg.text
    new_doc = reinsert_segments(
        doc,
        segments,
        {heading_seg.id: "Description of fields in the response"},
    )
    out = render_markdown(new_doc, target_lang="en")
    assert "### Description of fields in the response {#fields-Description}" in out


def test_build_heading_anchor_map_auto_and_explicit():
    ru = parse_markdown(
        "## Векторный поиск\n\n"
        "[jump](#векторный-поиск)\n\n"
        "### Поля {#fields-Описание}\n"
    )
    en = parse_markdown(
        "## Vector search\n\n"
        "[jump](#векторный-поиск)\n\n"
        "### Fields {#fields-Description}\n"
    )
    mapping = build_heading_anchor_map(ru, en)
    assert mapping["векторный-поиск"] == "vector-search"
    assert mapping["fields-Описание"] == "fields-Description"


def test_localize_links_remaps_in_page_fragment_from_heading_map():
    ru = parse_markdown("## Векторный поиск\n\n[Vector search](#векторный-поиск)\n")
    en = parse_markdown("## Vector search\n\n[Vector search](#векторный-поиск)\n")
    localize_links_in_document(en, target_lang="en", source_doc=ru)
    out = render_markdown(en)
    assert "[Vector search](#vector-search)" in out


def test_localize_links_remaps_path_fragment():
    ru = parse_markdown("## Векторный поиск\n\nSee [x](page.md#векторный-поиск).\n")
    en = parse_markdown("## Vector search\n\nSee [x](page.md#векторный-поиск).\n")
    localize_links_in_document(en, target_lang="en", source_doc=ru)
    out = render_markdown(en)
    assert "(page.md#vector-search)" in out


def test_vector_search_fixture_toc_link_remaps():
    """Regression #43997: same-page TOC link at top of vector-search.md."""
    ru = parse_markdown("# Векторный поиск\n\n- [Векторный поиск](#векторный-поиск)\n")
    en = parse_markdown("# Vector search\n\n- [Vector search](#векторный-поиск)\n")
    localize_links_in_document(en, target_lang="en", source_doc=ru)
    out = render_markdown(en)
    assert "[Vector search](#vector-search)" in out
    assert "векторный-поиск" not in out
