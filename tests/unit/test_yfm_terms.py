"""Tests for YFM term definitions and references."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.ast_types import (
    InlineTermRef,
    InlineText,
    Paragraph,
    TermDefinition,
)
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown


def round_trip(text: str) -> str:
    return render_markdown(parse_markdown(text))


def assert_stable(text: str) -> None:
    first = round_trip(text)
    second = round_trip(first)
    assert first == second, (
        f"Not stable.\n--- First ---\n{first!r}\n--- Second ---\n{second!r}"
    )


def test_term_definition_basic_ast():
    text = "[*cluster]: A set of nodes.\n"
    doc = parse_markdown(text)
    assert len(doc.children) == 1
    td = doc.children[0]
    assert isinstance(td, TermDefinition)
    assert td.term_id == "cluster"
    assert "".join(
        c.content for c in td.children if isinstance(c, InlineText)
    ) == "A set of nodes."


def test_term_ref_inline_ast():
    text = "See [*cluster] for details.\n"
    doc = parse_markdown(text)
    para = doc.children[0]
    assert isinstance(para, Paragraph)
    refs = [c for c in para.children if isinstance(c, InlineTermRef)]
    assert len(refs) == 1
    assert refs[0].term_id == "cluster"


def test_term_ref_and_definition_together():
    text = (
        "A YDB [*cluster] consists of nodes.\n"
        "\n"
        "[*cluster]: A cluster is a set of nodes.\n"
    )
    doc = parse_markdown(text)
    kinds = [c.kind for c in doc.children]
    assert kinds == ["paragraph", "term_definition"]


def test_not_a_term_ref_regular_link():
    text = "See [docs](http://x).\n"
    doc = parse_markdown(text)
    para = doc.children[0]
    refs = [c for c in para.children if isinstance(c, InlineTermRef)]
    assert refs == []


@pytest.mark.parametrize(
    "text",
    [
        "[*cluster]: A set of nodes.\n",
        "[*tablet]: A unit of distribution.\n",
        "See [*cluster] for details.\n",
        (
            "A [*cluster] is made of [*tablet]s.\n"
            "\n"
            "[*cluster]: Cluster definition.\n"
            "\n"
            "[*tablet]: Tablet definition.\n"
        ),
    ],
)
def test_round_trip_terms(text: str):
    assert_stable(text)

