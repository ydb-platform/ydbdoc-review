"""YFM ``#|`` / ``|#`` tables parse as GFM-equivalent Table IR (§6.147)."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.parsing.ast_types import Table
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.qa import gate_round_trip
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.extractor import extract_segments


def test_yfm_table_parses_to_table_ir():
    src = dedent(
        """
        # Title

        #|
        || A | B ||
        || 1 | `x` ||
        |#
        """
    ).strip()
    doc = parse_markdown(src)
    assert len(doc.children) == 2
    assert isinstance(doc.children[1], Table)
    table = doc.children[1]
    assert [c.children[0].content for c in table.header.cells] == ["A", "B"]  # type: ignore[attr-defined]
    assert len(table.rows) == 1
    rendered = render_markdown(doc)
    assert "| A | B |" in rendered
    assert "`x`" in rendered
    assert "AA" not in rendered


def test_yfm_and_gfm_same_content_align():
    yfm = dedent(
        """
        # kafka

        ## Params

        #|
        || Parameter | Type ||
        || `a` | bool ||
        || `b` | int ||
        |#
        """
    ).strip()
    gfm = dedent(
        """
        # kafka

        ## Params

        | Parameter | Type |
        | --- | --- |
        | `a` | bool |
        | `b` | int |
        """
    ).strip()
    segs = extract_segments(parse_markdown(yfm))
    _trans, err = gate_round_trip(segs, gfm)
    assert err is None
    assert len(segs) == len(extract_segments(parse_markdown(gfm)))
