"""Tests for YFM {% list tabs %} block construct."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.ast_types import (
    FencedCode,
    InlineText,
    Paragraph,
    YfmTab,
    YfmTabs,
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


def test_tabs_basic_ast():
    text = (
        "{% list tabs %}\n"
        "\n"
        "- Python\n"
        "\n"
        "  Python text.\n"
        "\n"
        "- Go\n"
        "\n"
        "  Go text.\n"
        "\n"
        "{% endlist %}\n"
    )
    doc = parse_markdown(text)
    assert len(doc.children) == 1
    tabs = doc.children[0]
    assert isinstance(tabs, YfmTabs)
    assert tabs.variant == "tabs"
    assert len(tabs.children) == 2

    py = tabs.children[0]
    go = tabs.children[1]

    title_py = "".join(c.content for c in py.title if isinstance(c, InlineText))
    title_go = "".join(c.content for c in go.title if isinstance(c, InlineText))
    assert title_py == "Python"
    assert title_go == "Go"

    assert len(py.children) >= 1
    assert isinstance(py.children[0], Paragraph)


def test_tabs_accordion_variant():
    text = (
        "{% list tabs accordion %}\n"
        "\n"
        "- One\n"
        "\n"
        "  Content one.\n"
        "\n"
        "{% endlist %}\n"
    )
    doc = parse_markdown(text)
    tabs = doc.children[0]
    assert isinstance(tabs, YfmTabs)
    assert tabs.variant == "tabs accordion"


def test_tabs_with_code_inside():
    text = (
        "{% list tabs %}\n"
        "\n"
        "- Bash\n"
        "\n"
        "  ```bash\n"
        "  echo hi\n"
        "  ```\n"
        "\n"
        "{% endlist %}\n"
    )
    doc = parse_markdown(text)
    tabs = doc.children[0]
    assert isinstance(tabs, YfmTabs)
    bash_tab = tabs.children[0]
    # Body should contain a fenced code block.
    kinds = [c.kind for c in bash_tab.children]
    assert "fenced_code" in kinds


def test_tabs_with_variable_in_title():
    text = (
        "{% list tabs %}\n"
        "\n"
        "- {{ engine-name }}\n"
        "\n"
        "  Content.\n"
        "\n"
        "{% endlist %}\n"
    )
    doc = parse_markdown(text)
    tabs = doc.children[0]
    assert isinstance(tabs, YfmTabs)
    tab = tabs.children[0]
    kinds = [n.kind for n in tab.title]
    assert "yfm_variable" in kinds


def test_tabs_unclosed_falls_back():
    text = "{% list tabs %}\n\n- Python\n\n  Content.\n"
    doc = parse_markdown(text)
    assert not any(isinstance(c, YfmTabs) for c in doc.children)


# --- Round-trip ---


@pytest.mark.parametrize(
    "text",
    [
        (
            "{% list tabs %}\n"
            "\n"
            "- Python\n"
            "\n"
            "  Python text.\n"
            "\n"
            "- Go\n"
            "\n"
            "  Go text.\n"
            "\n"
            "{% endlist %}\n"
        ),
        (
            "{% list tabs %}\n"
            "\n"
            "- Bash\n"
            "\n"
            "  Run this command:\n"
            "\n"
            "  ```bash\n"
            "  ydb scheme ls\n"
            "  ```\n"
            "\n"
            "{% endlist %}\n"
        ),
        (
            "{% list tabs accordion %}\n"
            "\n"
            "- Section 1\n"
            "\n"
            "  Content.\n"
            "\n"
            "{% endlist %}\n"
        ),
        (
            "Before tabs.\n"
            "\n"
            "{% list tabs %}\n"
            "\n"
            "- A\n"
            "\n"
            "  Text A.\n"
            "\n"
            "- B\n"
            "\n"
            "  Text B with `code`.\n"
            "\n"
            "{% endlist %}\n"
            "\n"
            "After tabs.\n"
        ),
    ],
)
def test_round_trip_tabs(text: str):
    assert_stable(text)


def test_round_trip_tabs_with_note_inside():
    text = (
        "{% list tabs %}\n"
        "\n"
        "- Python\n"
        "\n"
        "  {% note warning %}\n"
        "\n"
        "  Be careful.\n"
        "\n"
        "  {% endnote %}\n"
        "\n"
        "{% endlist %}\n"
    )
    assert_stable(text)

