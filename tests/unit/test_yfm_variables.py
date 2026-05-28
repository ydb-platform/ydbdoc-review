"""Tests for YFM {{ variable }} inline construct."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.ast_types import (
    Heading,
    InlineLink,
    InlineText,
    InlineVariable,
    Paragraph,
)
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown


def round_trip(text: str) -> str:
    return render_markdown(parse_markdown(text))


# --- AST shape ---


def test_variable_is_separate_node():
    doc = parse_markdown("Hello {{ name }} world.\n")
    para = doc.children[0]
    assert isinstance(para, Paragraph)
    kinds = [c.kind for c in para.children]
    assert kinds == ["text", "yfm_variable", "text"]


def test_variable_name_extracted():
    doc = parse_markdown("Use {{ ydb-short-name }} CLI.\n")
    para = doc.children[0]
    assert isinstance(para, Paragraph)
    var = para.children[1]
    assert isinstance(var, InlineVariable)
    assert var.name == "ydb-short-name"
    assert var.raw == "{{ ydb-short-name }}"


def test_variable_with_no_spaces():
    doc = parse_markdown("{{name}}\n")
    para = doc.children[0]
    assert isinstance(para, Paragraph)
    var = para.children[0]
    assert isinstance(var, InlineVariable)
    assert var.name == "name"
    assert var.raw == "{{name}}"


def test_variable_with_extra_spaces():
    doc = parse_markdown("{{  name  }}\n")
    para = doc.children[0]
    var = para.children[0]
    assert isinstance(var, InlineVariable)
    assert var.name == "name"
    assert var.raw == "{{  name  }}"


def test_variable_with_dots_and_dashes():
    doc = parse_markdown("{{ ydb-version.major }}\n")
    para = doc.children[0]
    var = para.children[0]
    assert isinstance(var, InlineVariable)
    assert var.name == "ydb-version.major"


def test_variable_in_heading():
    doc = parse_markdown("## Run {{ ydb-short-name }} CLI\n")
    h = doc.children[0]
    assert isinstance(h, Heading)
    kinds = [c.kind for c in h.children]
    assert "yfm_variable" in kinds


@pytest.mark.xfail(
    reason="markdown-it does not recognize {{ var }} inside link URLs. "
    "TODO: add custom link rule that accepts YFM variables in href."
)
def test_variable_in_link_url():
    doc = parse_markdown("[glossary]({{ link-glossary }})\n")
    para = doc.children[0]
    link = para.children[0]
    assert isinstance(link, InlineLink)
    assert link.href == "{{ link-glossary }}"


def test_variable_in_link_url_round_trip():
    """Even if not parsed as a link, the text must round-trip stably."""
    text = "[glossary]({{ link-glossary }})\n"
    out1 = round_trip(text)
    out2 = round_trip(out1)
    assert out1 == out2


def test_variable_in_link_text():
    doc = parse_markdown("[See {{ name }} docs](http://x)\n")
    para = doc.children[0]
    link = para.children[0]
    assert isinstance(link, InlineLink)
    kinds = [c.kind for c in link.children]
    assert "yfm_variable" in kinds


def test_variable_in_inline_code_is_text():
    """Inside `code` backticks, {{ var }} must NOT be parsed as a variable."""
    doc = parse_markdown("Use `{{ name }}` literally.\n")
    para = doc.children[0]
    code = para.children[1]
    assert code.kind == "code"
    assert code.content == "{{ name }}"  # type: ignore[attr-defined]


def test_variable_in_fenced_code_is_text():
    """Inside ``` blocks, {{ var }} must NOT be parsed as a variable."""
    text = "```\n{{ name }}\n```\n"
    doc = parse_markdown(text)
    fence = doc.children[0]
    assert fence.kind == "fenced_code"
    assert "{{ name }}" in fence.content  # type: ignore[attr-defined]


# --- Round-trip ---


@pytest.mark.parametrize(
    "text",
    [
        "{{ name }}\n",
        "{{name}}\n",
        "Hello {{ name }} world.\n",
        "## Title with {{ var }}\n",
        "Use {{ ydb-short-name }} CLI to run.\n",
        "Multiple {{ a }} and {{ b }} and {{ c }} here.\n",
        "[Link]({{ url-var }})\n",
        "[Link text {{ var }}](http://x)\n",
        "- list with {{ var }} item\n- another {{ x }}\n",
        "| col | val |\n| --- | --- |\n| {{ a }} | {{ b }} |\n",
    ],
)
def test_round_trip_variables(text: str):
    out1 = round_trip(text)
    out2 = round_trip(out1)
    assert out1 == out2, f"Not stable.\nFirst:\n{out1!r}\nSecond:\n{out2!r}"


def test_variable_preserves_exact_spacing():
    """Test that raw spacing is preserved (important for diffs)."""
    inputs = [
        "{{ name }}",
        "{{name}}",
        "{{  name  }}",
        "{{ name}}",
        "{{name }}",
    ]
    for raw in inputs:
        text = f"{raw}\n"
        out = round_trip(text)
        assert out == text, f"Spacing changed: {text!r} → {out!r}"


# --- Edge cases ---


def test_not_a_variable_single_brace():
    """Single { ... } must not be parsed as a variable."""
    doc = parse_markdown("This is { not a var }.\n")
    para = doc.children[0]
    # All content should be text.
    for child in para.children:
        assert child.kind in ("text", "softbreak")


def test_not_a_variable_just_braces():
    doc = parse_markdown("{{ }}\n")
    # Empty name — should NOT match.
    para = doc.children[0]
    var_kinds = [c.kind for c in para.children if c.kind == "yfm_variable"]
    assert len(var_kinds) == 0

