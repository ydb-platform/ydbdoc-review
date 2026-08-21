"""Percent-encoded protect markers must reinsert (#48764)."""

from __future__ import annotations

from ydbdoc_review.parsing.ast_types import InlineLink, InlineText, InlineVariable
from ydbdoc_review.segmentation.reinsert import _split_text_by_placeholders
from ydbdoc_review.validation.homoglyphs import decode_percent_encoded_protect_markers


def test_split_restores_percent_encoded_url_placeholder():
    link = InlineLink(href="../authorization.md#sid", children=[])
    mapping = {"⟦U1⟧": link}
    nodes = _split_text_by_placeholders(
        "uses a [SID](%E2%9F%A6U1%E2%9F%A7) for user identification",
        mapping,
    )
    assert any(isinstance(n, InlineLink) and n.href.endswith("#sid") for n in nodes)
    assert not any(
        isinstance(n, InlineText) and ("%E2%9F%A6" in n.content or "⟦U1⟧" in n.content)
        for n in nodes
    )


def test_split_restores_literal_variable_placeholder():
    var = InlineVariable(name="ydb-short-name", raw="{{ ydb-short-name }}")
    mapping = {"⟦V1⟧": var}
    nodes = _split_text_by_placeholders("Security model in ⟦V1⟧ introduces", mapping)
    assert any(isinstance(n, InlineVariable) for n in nodes)


def test_decode_percent_encoded_protect_markers():
    raw = "![diag](%E2%9F%A6S1%E2%9F%A7) and [x](%e2%9f%a6U2%e2%9f%a7)"
    assert decode_percent_encoded_protect_markers(raw) == "![diag](⟦S1⟧) and [x](⟦U2⟧)"


def test_substitute_expands_glued_marker_inside_inline_code():
    """§6.192: glued tails on code atoms must restore (#37673)."""
    from ydbdoc_review.parsing.ast_types import InlineCode
    from ydbdoc_review.segmentation.reinsert import _substitute_placeholders

    # Prefix atom (model treated marker as ``tracing`` only).
    nodes = _substitute_placeholders(
        [InlineCode(content="⟦C3⟧_subscriber::fmt")],
        {"⟦C3⟧": InlineCode(content="tracing")},
    )
    assert nodes[0].content == "tracing_subscriber::fmt"

    # Full atom + duplicated tail after the marker.
    nodes = _substitute_placeholders(
        [InlineCode(content="⟦C3⟧_subscriber::fmt")],
        {"⟦C3⟧": InlineCode(content="tracing_subscriber::fmt")},
    )
    assert nodes[0].content == "tracing_subscriber::fmt"
