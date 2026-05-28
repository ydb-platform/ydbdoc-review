"""Round-trip tests: parse → render → parse → render must be stable."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown


def round_trip(text: str) -> str:
    """Parse then render once."""
    return render_markdown(parse_markdown(text))


def assert_stable(text: str) -> None:
    """First pass normalizes; second pass must be identical."""
    first = round_trip(text)
    second = round_trip(first)
    assert first == second, (
        f"Round-trip not stable.\n--- First ---\n{first!r}\n--- Second ---\n{second!r}"
    )


# --- Headings ---


def test_heading_basic():
    text = "# Hello\n"
    assert_stable(text)


def test_heading_levels():
    text = "# H1\n\n## H2\n\n### H3\n\n#### H4\n\n##### H5\n\n###### H6\n"
    assert_stable(text)


def test_heading_with_anchor():
    text = "## Examples {#examples}\n"
    out = round_trip(text)
    assert "{#examples}" in out
    assert_stable(text)


def test_heading_with_inline_code():
    text = "## The `code` heading\n"
    assert_stable(text)


# --- Paragraphs ---


def test_simple_paragraph():
    text = "Hello, world.\n"
    assert_stable(text)


def test_paragraph_with_emphasis():
    text = "This is *italic* and **bold** text.\n"
    assert_stable(text)


def test_paragraph_with_inline_code():
    text = "Use `--input-file` to specify input.\n"
    assert_stable(text)


def test_paragraph_with_link():
    text = "See [docs](https://example.com) for details.\n"
    assert_stable(text)


def test_paragraph_with_link_and_title():
    text = 'See [docs](https://example.com "Documentation") for details.\n'
    assert_stable(text)


def test_paragraph_with_image():
    text = "![alt text](image.png)\n"
    assert_stable(text)


# --- Code blocks ---


def test_fenced_code_basic():
    text = "```\nhello\n```\n"
    assert_stable(text)


def test_fenced_code_with_lang():
    text = "```python\nprint('hello')\n```\n"
    assert_stable(text)


def test_fenced_code_with_bash():
    text = "```bash\necho hello\n```\n"
    assert_stable(text)


def test_fenced_code_multiline():
    text = "```python\ndef foo():\n    return 42\n\nprint(foo())\n```\n"
    assert_stable(text)


# --- Lists ---


def test_bullet_list_simple():
    text = "- one\n- two\n- three\n"
    assert_stable(text)


def test_bullet_list_with_star():
    text = "* one\n* two\n"
    out = round_trip(text)
    # We normalize the marker, that's fine.
    assert "one" in out and "two" in out
    assert_stable(text)


def test_ordered_list_simple():
    text = "1. one\n2. two\n3. three\n"
    assert_stable(text)


def test_nested_bullet_list():
    text = "- outer\n  - inner\n  - inner2\n- outer2\n"
    assert_stable(text)


def test_list_with_paragraph_items():
    text = (
        "- First item with a longer description.\n"
        "- Second item.\n"
    )
    assert_stable(text)


# --- Block quotes ---


def test_blockquote_simple():
    text = "> hello\n"
    assert_stable(text)


def test_blockquote_multiline():
    text = "> line one\n> line two\n"
    assert_stable(text)


# --- Thematic break ---


def test_thematic_break():
    text = "before\n\n---\n\nafter\n"
    assert_stable(text)


# --- Tables ---


def test_table_basic():
    text = (
        "| A | B |\n"
        "| --- | --- |\n"
        "| 1 | 2 |\n"
        "| 3 | 4 |\n"
    )
    assert_stable(text)


def test_table_with_inline_code():
    text = (
        "| Flag | Description |\n"
        "| --- | --- |\n"
        "| `--yaml` | Output as YAML |\n"
        "| `--json` | Output as JSON |\n"
    )
    out = round_trip(text)
    assert "`--yaml`" in out
    assert "`--json`" in out
    assert_stable(text)


# --- HTML ---


def test_html_block():
    text = "<div>\nhello\n</div>\n"
    assert_stable(text)


def test_inline_html():
    text = "Text with <br/> a break.\n"
    assert_stable(text)


# --- Mixed ---


def test_mixed_document():
    text = (
        "# Title\n"
        "\n"
        "Intro paragraph.\n"
        "\n"
        "## Section\n"
        "\n"
        "- item with `code`\n"
        "- item with [link](http://x)\n"
        "\n"
        "```bash\n"
        "echo hi\n"
        "```\n"
        "\n"
        "End.\n"
    )
    assert_stable(text)


# --- Edge cases that should NOT damage CLI flags ---


def test_cli_flag_not_damaged_in_inline_code():
    text = "Use `--yaml` option.\n"
    out = round_trip(text)
    assert "`--yaml`" in out
    assert "-- yaml" not in out


def test_cli_flag_in_table():
    text = (
        "| Option | Meaning |\n"
        "| --- | --- |\n"
        "| `--input-format` | Format of the input |\n"
    )
    out = round_trip(text)
    assert "`--input-format`" in out
    assert "-- input-format" not in out
    assert "--input - format" not in out


@pytest.mark.parametrize(
    "text",
    [
        "Just text.\n",
        "# Heading only\n",
        "```\ncode only\n```\n",
        "- list\n- only\n",
    ],
)
def test_minimal_documents(text: str):
    assert_stable(text)
