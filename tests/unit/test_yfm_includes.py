"""Tests for YFM {% include %} block construct."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.ast_types import YfmInclude
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


def test_include_basic_ast():
    text = "{% include [my fragment](../_includes/foo.md) %}\n"
    doc = parse_markdown(text)
    assert len(doc.children) == 1
    inc = doc.children[0]
    assert isinstance(inc, YfmInclude)
    assert inc.text == "my fragment"
    assert inc.path == "../_includes/foo.md"
    assert inc.notitle is False


def test_include_notitle():
    text = "{% include notitle [text](path/to/x.md) %}\n"
    doc = parse_markdown(text)
    inc = doc.children[0]
    assert isinstance(inc, YfmInclude)
    assert inc.notitle is True
    assert inc.text == "text"
    assert inc.path == "path/to/x.md"


def test_include_with_absolute_path():
    text = "{% include [doc](/ydb/docs/_includes/auth.md) %}\n"
    doc = parse_markdown(text)
    inc = doc.children[0]
    assert isinstance(inc, YfmInclude)
    assert inc.path == "/ydb/docs/_includes/auth.md"


def test_include_with_empty_text():
    text = "{% include [](path.md) %}\n"
    doc = parse_markdown(text)
    inc = doc.children[0]
    assert isinstance(inc, YfmInclude)
    assert inc.text == ""
    assert inc.path == "path.md"


def test_include_in_context():
    text = (
        "Intro paragraph.\n"
        "\n"
        "{% include [frag](../inc.md) %}\n"
        "\n"
        "After paragraph.\n"
    )
    doc = parse_markdown(text)
    kinds = [c.kind for c in doc.children]
    assert kinds == ["paragraph", "yfm_include", "paragraph"]


# --- Round-trip ---


@pytest.mark.parametrize(
    "text",
    [
        "{% include [text](path.md) %}\n",
        "{% include notitle [text](path.md) %}\n",
        "{% include [my fragment](../_includes/foo.md) %}\n",
        "{% include notitle [doc](/abs/path/to/file.md) %}\n",
        "{% include [](empty-text.md) %}\n",
        (
            "Before.\n"
            "\n"
            "{% include [a](one.md) %}\n"
            "\n"
            "Middle.\n"
            "\n"
            "{% include notitle [b](two.md) %}\n"
            "\n"
            "After.\n"
        ),
    ],
)
def test_round_trip_includes(text: str):
    assert_stable(text)


def test_include_inside_note():
    text = (
        "{% note info %}\n"
        "\n"
        "{% include [frag](../inc.md) %}\n"
        "\n"
        "{% endnote %}\n"
    )
    assert_stable(text)


def test_include_inside_tab():
    text = (
        "{% list tabs %}\n"
        "\n"
        "- Python\n"
        "\n"
        "  {% include [py](../py.md) %}\n"
        "\n"
        "{% endlist %}\n"
    )
    assert_stable(text)


# --- Edge cases ---


def test_include_with_trailing_spaces_in_directive():
    text = "{% include  [text](path.md)  %}\n"
    doc = parse_markdown(text)
    inc = doc.children[0]
    assert isinstance(inc, YfmInclude)
    # Round-trip normalizes whitespace inside the directive.
    out = round_trip(text)
    assert "{% include [text](path.md) %}" in out
    assert_stable(text)


def test_not_an_include_missing_brackets():
    """Without proper [text](path) shape, it's not a recognized include."""
    text = "{% include something %}\n"
    doc = parse_markdown(text)
    # Falls back to a plain paragraph; no YfmInclude.
    assert not any(c.kind == "yfm_include" for c in doc.children)


def test_include_not_in_inline_code():
    """An include-looking string inside backticks must NOT be parsed as include."""
    text = "Use `{% include [x](y.md) %}` in your code.\n"
    doc = parse_markdown(text)
    # Should be a single paragraph with inline code, no include block.
    assert len(doc.children) == 1
    para = doc.children[0]
    assert para.kind == "paragraph"
    kinds = [c.kind for c in para.children]
    assert "code" in kinds
    assert "yfm_include" not in kinds

