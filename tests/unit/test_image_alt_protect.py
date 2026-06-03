"""Tests for translatable image alt with protected src."""

from __future__ import annotations

import copy

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.reinsert import reinsert_segments
from ydbdoc_review.rendering.markdown_renderer import render_markdown


def test_image_alt_is_translatable_src_protected():
    doc = parse_markdown(
        "![Ручная установка, узлы](../../_assets/manual_installation_1.png)\n"
    )
    segments = extract_segments(doc)
    seg = segments[0]
    assert "⟦S1⟧" in seg.text
    assert "Ручная установка" in seg.text
    assert "⟦I" not in seg.text

    doc2 = copy.deepcopy(doc)
    reinsert_segments(
        doc2,
        segments,
        {seg.id: "![Manual installation, nodes](⟦S1⟧)"},
    )
    rendered = render_markdown(doc2)
    assert "Manual installation" in rendered
    assert "manual_installation_1.png" in rendered
    assert "Ручная" not in rendered
