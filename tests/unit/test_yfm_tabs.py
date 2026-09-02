"""Tests for YFM {% list tabs %} block construct."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.ast_types import (
    AmbiguousYfmStructureError,
    InlineText,
    Paragraph,
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


def test_tabs_group_lang_variant_roundtrip():
    """§6.194: ``group=lang`` must parse as YfmTabs (#37673 health-check / topic)."""
    text = (
        "{% list tabs group=lang %}\n"
        "\n"
        "- Go\n"
        "\n"
        "  Go body.\n"
        "\n"
        "- Python\n"
        "\n"
        "  Python body.\n"
        "\n"
        "{% endlist %}\n"
    )
    doc = parse_markdown(text)
    assert len(doc.children) == 1
    tabs = doc.children[0]
    assert isinstance(tabs, YfmTabs)
    assert tabs.variant == "tabs group=lang"
    assert len(tabs.children) == 2
    assert_stable(text)
    assert "{% list tabs group=lang %}" in round_trip(text)


def test_tabs_group_hyphenated_value():
    text = (
        "{% list tabs group=manual-systemd %}\n"
        "\n"
        "- Unit\n"
        "\n"
        "  Body.\n"
        "\n"
        "{% endlist %}\n"
    )
    doc = parse_markdown(text)
    tabs = doc.children[0]
    assert isinstance(tabs, YfmTabs)
    assert tabs.variant == "tabs group=manual-systemd"
    assert_stable(text)


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


def test_direct_depth_leading_content_is_rejected_before_tab_ownership() -> None:
    text = (
        "{% list tabs %}\n"
        "orphan at container depth\n"
        "- Python\n"
        "  body\n"
        "{% endlist %}\n"
    )
    with pytest.raises(AmbiguousYfmStructureError, match="unowned direct-depth"):
        parse_markdown(text)


def test_unindented_sibling_between_source_owned_tab_ranges_stays_in_that_tab() -> None:
    text = (
        "{% list tabs %}\n"
        "- Python\n"
        "unindented source-owned body\n"
        "- Go\n"
        "  indented body\n"
        "{% endlist %}\n"
    )
    tabs = parse_markdown(text).children[0]
    assert isinstance(tabs, YfmTabs)
    assert [tab.title[0].content for tab in tabs.children] == ["Python", "Go"]
    assert "unindented source-owned body" in round_trip(text)


def test_nested_tabs_are_child_container_tokens_not_outer_tab_headers() -> None:
    text = (
        "{% list tabs %}\n"
        "- Outer\n"
        "\n"
        "  {% list tabs %}\n"
        "  - Inner\n"
        "    inner body\n"
        "  {% endlist %}\n"
        "{% endlist %}\n"
    )
    outer = parse_markdown(text).children[0]
    assert isinstance(outer, YfmTabs)
    assert len(outer.children) == 1
    nested = next(child for child in outer.children[0].children if isinstance(child, YfmTabs))
    assert nested.parent_container_id == outer.container_id
    assert nested.children[0].title[0].content == "Inner"
    assert_stable(text)


def test_tab_spans_use_utf8_offsets_and_keep_empty_tabs() -> None:
    text = "{% list tabs %}\n- Ё\n{% endlist %}\n"
    tabs = parse_markdown(text).children[0]
    assert isinstance(tabs, YfmTabs)
    tab = tabs.children[0]
    assert tab.children == []
    assert tab.title_span is not None
    assert tab.title_span.byte_start == len(b"{% list tabs %}\n- ")
    assert tab.title_span.byte_end == tab.title_span.byte_start + len("Ё".encode())


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
