"""Tests for YFM {% note %} block construct."""

# ruff: noqa: RUF001

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.ast_types import (
    Paragraph,
    YfmNote,
)
from ydbdoc_review.parsing.markdown_parser import create_parser, parse_markdown
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


def test_note_basic_ast():
    text = "{% note info %}\n\nHello.\n\n{% endnote %}\n"
    doc = parse_markdown(text)
    assert len(doc.children) == 1
    note = doc.children[0]
    assert isinstance(note, YfmNote)
    assert note.note_type == "info"
    assert note.title is None
    assert len(note.children) == 1
    assert isinstance(note.children[0], Paragraph)


def test_note_types():
    for ntype in ["info", "tip", "warning", "alert", "important"]:
        text = f"{{% note {ntype} %}}\n\nText.\n\n{{% endnote %}}\n"
        doc = parse_markdown(text)
        note = doc.children[0]
        assert isinstance(note, YfmNote)
        assert note.note_type == ntype


def test_note_with_title():
    text = '{% note warning "Be Careful" %}\n\nDanger.\n\n{% endnote %}\n'
    doc = parse_markdown(text)
    note = doc.children[0]
    assert isinstance(note, YfmNote)
    assert note.note_type == "warning"
    assert note.title == "Be Careful"


def test_note_with_heading_inside():
    text = (
        "{% note info %}\n"
        "\n"
        "## Sub-heading\n"
        "\n"
        "Some text.\n"
        "\n"
        "{% endnote %}\n"
    )
    doc = parse_markdown(text)
    note = doc.children[0]
    assert isinstance(note, YfmNote)
    kinds = [c.kind for c in note.children]
    assert "heading" in kinds


def test_note_with_code_inside():
    text = (
        "{% note warning %}\n"
        "\n"
        "Run this:\n"
        "\n"
        "```bash\n"
        "echo hi\n"
        "```\n"
        "\n"
        "{% endnote %}\n"
    )
    doc = parse_markdown(text)
    note = doc.children[0]
    assert isinstance(note, YfmNote)
    kinds = [c.kind for c in note.children]
    assert "fenced_code" in kinds


def test_nested_notes():
    text = (
        "{% note info %}\n"
        "\n"
        "Outer text.\n"
        "\n"
        "{% note warning %}\n"
        "\n"
        "Inner warning.\n"
        "\n"
        "{% endnote %}\n"
        "\n"
        "Outer continued.\n"
        "\n"
        "{% endnote %}\n"
    )
    doc = parse_markdown(text)
    outer = doc.children[0]
    assert isinstance(outer, YfmNote)
    assert outer.note_type == "info"
    inner_notes = [c for c in outer.children if isinstance(c, YfmNote)]
    assert len(inner_notes) == 1
    assert inner_notes[0].note_type == "warning"


def test_note_with_variable_inside():
    text = (
        "{% note tip %}\n"
        "\n"
        "Use {{ ydb-short-name }} CLI.\n"
        "\n"
        "{% endnote %}\n"
    )
    doc = parse_markdown(text)
    note = doc.children[0]
    assert isinstance(note, YfmNote)
    para = note.children[0]
    assert isinstance(para, Paragraph)
    kinds = [c.kind for c in para.children]
    assert "yfm_variable" in kinds


def test_unclosed_note_falls_back_to_paragraphs():
    """If {% endnote %} is missing, the block should fall back to plain parsing."""
    text = "{% note info %}\n\nSome text.\n\nNo closing tag.\n"
    doc = parse_markdown(text)
    # Should not crash. The opening line stays as a paragraph.
    assert not any(isinstance(c, YfmNote) for c in doc.children)


# --- Round-trip ---


@pytest.mark.parametrize(
    "text",
    [
        "{% note info %}\n\nHello.\n\n{% endnote %}\n",
        "{% note warning %}\n\nDanger.\n\n{% endnote %}\n",
        "{% note tip %}\n\nUse `--yaml` for output.\n\n{% endnote %}\n",
        '{% note info "Title here" %}\n\nText.\n\n{% endnote %}\n',
        (
            "Intro paragraph.\n"
            "\n"
            "{% note warning %}\n"
            "\n"
            "Warning text.\n"
            "\n"
            "{% endnote %}\n"
            "\n"
            "After paragraph.\n"
        ),
        (
            "{% note info %}\n"
            "\n"
            "First para.\n"
            "\n"
            "Second para.\n"
            "\n"
            "{% endnote %}\n"
        ),
        (
            "{% note tip %}\n"
            "\n"
            "Use {{ ydb-short-name }} for queries.\n"
            "\n"
            "{% endnote %}\n"
        ),
    ],
)
def test_round_trip_notes(text: str):
    assert_stable(text)


def test_round_trip_nested_notes():
    text = (
        "{% note info %}\n"
        "\n"
        "Outer.\n"
        "\n"
        "{% note warning %}\n"
        "\n"
        "Inner.\n"
        "\n"
        "{% endnote %}\n"
        "\n"
        "{% endnote %}\n"
    )
    assert_stable(text)


def test_round_trip_note_with_code():
    text = (
        "{% note warning %}\n"
        "\n"
        "Run:\n"
        "\n"
        "```bash\n"
        "ydb scheme ls\n"
        "```\n"
        "\n"
        "{% endnote %}\n"
    )
    assert_stable(text)


def test_round_trip_note_with_list():
    text = (
        "{% note info %}\n"
        "\n"
        "Items:\n"
        "\n"
        "- one\n"
        "- two\n"
        "\n"
        "{% endnote %}\n"
    )
    assert_stable(text)
@pytest.mark.parametrize("title", ["Заголовок", ""])
def test_note_title_span_excludes_quotes_and_uses_utf8(title):
    text = f'{{% note info "{title}" %}}\nText\n{{% endnote %}}\n'
    token = create_parser().parse(text)[0]
    span = token.meta["title_span"]
    assert text.encode()[span["byte_start"] : span["byte_end"]] == title.encode()
    assert text.encode()[span["byte_start"] - 1 : span["byte_start"]] == b'"'
    assert text.encode()[span["byte_end"] : span["byte_end"] + 1] == b'"'


def test_note_without_title_has_no_title_span():
    token = create_parser().parse("{% note info %}\nText\n{% endnote %}\n")[0]
    assert "title_span" not in token.meta


def test_indented_cyrillic_adjacent_note_owns_exact_marker_spans():
    text = (
        "Перед\n"
        "\n"
        "  {% note warning \"Заголовок\" %}\n"
        "  Текст рядом с кириллицей.\n"
        "  {% endnote %}\n"
        "\n"
        "После\n"
    )
    tokens = [token for token in create_parser().parse(text) if token.type.startswith("yfm_note")]
    open_token, close_token = tokens[0], tokens[-1]
    open_span = open_token.meta["source_span"]
    close_span = close_token.meta["source_span"]
    encoded = text.encode()
    assert encoded[open_span["byte_start"] : open_span["byte_end"]].startswith(
        b"{% note warning"
    )
    assert encoded[close_span["byte_start"] : close_span["byte_end"]] == b"{% endnote %}"
    title = open_token.meta["title_span"]
    assert encoded[title["byte_start"] : title["byte_end"]] == "Заголовок".encode()
