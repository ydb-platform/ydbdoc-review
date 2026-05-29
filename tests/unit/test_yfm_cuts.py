"""Tests for YFM {% cut %} block construct."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.ast_types import (
    FencedCode,
    Paragraph,
    YfmCut,
    YfmNote,
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


# --- AST shape ---


def test_cut_basic_ast():
    text = '{% cut "Click me" %}\n\nHidden text.\n\n{% endcut %}\n'
    doc = parse_markdown(text)
    assert len(doc.children) == 1
    cut = doc.children[0]
    assert isinstance(cut, YfmCut)
    assert cut.title == "Click me"
    assert len(cut.children) == 1
    assert isinstance(cut.children[0], Paragraph)


def test_cut_with_empty_title():
    text = '{% cut "" %}\n\nText.\n\n{% endcut %}\n'
    doc = parse_markdown(text)
    cut = doc.children[0]
    assert isinstance(cut, YfmCut)
    assert cut.title == ""


def test_cut_with_code_inside():
    text = (
        '{% cut "Show example" %}\n'
        "\n"
        "```bash\n"
        "echo hi\n"
        "```\n"
        "\n"
        "{% endcut %}\n"
    )
    doc = parse_markdown(text)
    cut = doc.children[0]
    assert isinstance(cut, YfmCut)
    kinds = [c.kind for c in cut.children]
    assert "fenced_code" in kinds


def test_nested_cuts():
    text = (
        '{% cut "Outer" %}\n'
        "\n"
        "Outer text.\n"
        "\n"
        '{% cut "Inner" %}\n'
        "\n"
        "Inner text.\n"
        "\n"
        "{% endcut %}\n"
        "\n"
        "{% endcut %}\n"
    )
    doc = parse_markdown(text)
    outer = doc.children[0]
    assert isinstance(outer, YfmCut)
    inner_cuts = [c for c in outer.children if isinstance(c, YfmCut)]
    assert len(inner_cuts) == 1
    assert inner_cuts[0].title == "Inner"


def test_cut_inside_note():
    text = (
        "{% note info %}\n"
        "\n"
        '{% cut "Details" %}\n'
        "\n"
        "Hidden.\n"
        "\n"
        "{% endcut %}\n"
        "\n"
        "{% endnote %}\n"
    )
    doc = parse_markdown(text)
    note = doc.children[0]
    assert isinstance(note, YfmNote)
    cuts = [c for c in note.children if isinstance(c, YfmCut)]
    assert len(cuts) == 1


def test_cut_with_variable_in_body():
    text = (
        '{% cut "Use CLI" %}\n'
        "\n"
        "Run {{ ydb-short-name }} CLI to begin.\n"
        "\n"
        "{% endcut %}\n"
    )
    doc = parse_markdown(text)
    cut = doc.children[0]
    assert isinstance(cut, YfmCut)
    para = cut.children[0]
    assert isinstance(para, Paragraph)
    kinds = [c.kind for c in para.children]
    assert "yfm_variable" in kinds


def test_unclosed_cut_falls_back():
    text = '{% cut "title" %}\n\nText.\n\nNo endcut.\n'
    doc = parse_markdown(text)
    assert not any(isinstance(c, YfmCut) for c in doc.children)


# --- Round-trip ---


@pytest.mark.parametrize(
    "text",
    [
        '{% cut "Show" %}\n\nHidden.\n\n{% endcut %}\n',
        '{% cut "Detailed example" %}\n\nFirst.\n\nSecond.\n\n{% endcut %}\n',
        (
            "Before.\n"
            "\n"
            '{% cut "Title" %}\n'
            "\n"
            "Inside cut.\n"
            "\n"
            "{% endcut %}\n"
            "\n"
            "After.\n"
        ),
        (
            '{% cut "Run this" %}\n'
            "\n"
            "```bash\n"
            "ydb scheme ls\n"
            "```\n"
            "\n"
            "{% endcut %}\n"
        ),
    ],
)
def test_round_trip_cuts(text: str):
    assert_stable(text)


def test_round_trip_nested_cuts():
    text = (
        '{% cut "Outer" %}\n'
        "\n"
        "Outer.\n"
        "\n"
        '{% cut "Inner" %}\n'
        "\n"
        "Inner.\n"
        "\n"
        "{% endcut %}\n"
        "\n"
        "{% endcut %}\n"
    )
    assert_stable(text)


def test_round_trip_cut_inside_tabs():
    text = (
        "{% list tabs %}\n"
        "\n"
        "- Python\n"
        "\n"
        '  {% cut "Show example" %}\n'
        "\n"
        "  Inside.\n"
        "\n"
        "  {% endcut %}\n"
        "\n"
        "{% endlist %}\n"
    )
    assert_stable(text)

