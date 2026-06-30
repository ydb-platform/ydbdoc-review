"""Tests for YFM heading anchor helpers."""

from __future__ import annotations

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.reinsert import reinsert_segments
from ydbdoc_review.segmentation.types import SegmentKind
from ydbdoc_review.validation.yfm_anchor import english_yfm_anchor


def test_english_yfm_anchor_translates_cyrillic_suffix():
    assert (
        english_yfm_anchor("fields-Описание", "Description of fields in the response")
        == "fields-Description"
    )


def test_english_yfm_anchor_keeps_ascii():
    assert english_yfm_anchor("examples", "Examples") == "examples"


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
