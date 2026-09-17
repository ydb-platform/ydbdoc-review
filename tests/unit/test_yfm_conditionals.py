"""Tests for YFM {% if %} ... {% endif %} block construct."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.ast_types import (
    Paragraph,
    YfmIf,
    YfmIfBranch,
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


def test_if_basic_ast():
    text = (
        "{% if oss %}\n"
        "\n"
        "OSS only.\n"
        "\n"
        "{% endif %}\n"
    )
    doc = parse_markdown(text)
    assert len(doc.children) == 1
    node = doc.children[0]
    assert isinstance(node, YfmIf)
    assert len(node.branches) == 1
    assert node.branches[0].condition == "oss"
    assert len(node.branches[0].children) == 1
    assert isinstance(node.branches[0].children[0], Paragraph)


def test_if_with_complex_condition():
    text = (
        '{% if audience == "enterprise" %}\n'
        "\n"
        "Enterprise text.\n"
        "\n"
        "{% endif %}\n"
    )
    doc = parse_markdown(text)
    node = doc.children[0]
    assert isinstance(node, YfmIf)
    assert node.branches[0].condition == 'audience == "enterprise"'


def test_if_else():
    text = (
        "{% if oss %}\n"
        "\n"
        "OSS.\n"
        "\n"
        "{% else %}\n"
        "\n"
        "Enterprise.\n"
        "\n"
        "{% endif %}\n"
    )
    doc = parse_markdown(text)
    node = doc.children[0]
    assert isinstance(node, YfmIf)
    assert len(node.branches) == 2
    assert node.branches[0].condition == "oss"
    assert node.branches[1].condition is None


def test_if_elsif_else():
    text = (
        '{% if mode == "a" %}\n'
        "\n"
        "A text.\n"
        "\n"
        '{% elsif mode == "b" %}\n'
        "\n"
        "B text.\n"
        "\n"
        "{% else %}\n"
        "\n"
        "Other.\n"
        "\n"
        "{% endif %}\n"
    )
    doc = parse_markdown(text)
    node = doc.children[0]
    assert isinstance(node, YfmIf)
    assert len(node.branches) == 3
    assert node.branches[0].condition == 'mode == "a"'
    assert node.branches[1].condition == 'mode == "b"'
    assert node.branches[2].condition is None


def test_nested_ifs():
    text = (
        "{% if outer %}\n"
        "\n"
        "Outer text.\n"
        "\n"
        "{% if inner %}\n"
        "\n"
        "Inner text.\n"
        "\n"
        "{% endif %}\n"
        "\n"
        "{% endif %}\n"
    )
    doc = parse_markdown(text)
    outer = doc.children[0]
    assert isinstance(outer, YfmIf)
    inner_nodes = [
        c for c in outer.branches[0].children if isinstance(c, YfmIf)
    ]
    assert len(inner_nodes) == 1
    assert inner_nodes[0].branches[0].condition == "inner"


def test_if_with_variable_inside():
    text = (
        "{% if oss %}\n"
        "\n"
        "Use {{ ydb-short-name }} community edition.\n"
        "\n"
        "{% endif %}\n"
    )
    doc = parse_markdown(text)
    node = doc.children[0]
    assert isinstance(node, YfmIf)
    para = node.branches[0].children[0]
    assert isinstance(para, Paragraph)
    kinds = [c.kind for c in para.children]
    assert "yfm_variable" in kinds


def test_unclosed_if_falls_back():
    text = "{% if oss %}\n\nSome content.\n\nNo endif.\n"
    doc = parse_markdown(text)
    assert not any(isinstance(c, YfmIf) for c in doc.children)


# --- Round-trip ---


@pytest.mark.parametrize(
    "text",
    [
        "{% if oss %}\n\nOSS only.\n\n{% endif %}\n",
        '{% if audience == "enterprise" %}\n\nEnt.\n\n{% endif %}\n',
        (
            "{% if oss %}\n"
            "\n"
            "OSS.\n"
            "\n"
            "{% else %}\n"
            "\n"
            "Ent.\n"
            "\n"
            "{% endif %}\n"
        ),
        (
            '{% if mode == "a" %}\n'
            "\n"
            "A.\n"
            "\n"
            '{% elsif mode == "b" %}\n'
            "\n"
            "B.\n"
            "\n"
            "{% else %}\n"
            "\n"
            "Other.\n"
            "\n"
            "{% endif %}\n"
        ),
        (
            "Before.\n"
            "\n"
            "{% if oss %}\n"
            "\n"
            "OSS section with {{ ydb-short-name }}.\n"
            "\n"
            "{% endif %}\n"
            "\n"
            "After.\n"
        ),
    ],
)
def test_round_trip_ifs(text: str):
    assert_stable(text)


def test_round_trip_nested_ifs():
    text = (
        "{% if outer %}\n"
        "\n"
        "Outer.\n"
        "\n"
        "{% if inner %}\n"
        "\n"
        "Inner.\n"
        "\n"
        "{% endif %}\n"
        "\n"
        "{% endif %}\n"
    )
    assert_stable(text)


def test_round_trip_if_with_code():
    text = (
        "{% if oss %}\n"
        "\n"
        "```bash\n"
        "echo hi\n"
        "```\n"
        "\n"
        "{% endif %}\n"
    )
    assert_stable(text)


def test_round_trip_if_inside_note():
    text = (
        "{% note info %}\n"
        "\n"
        "{% if oss %}\n"
        "\n"
        "OSS-only note.\n"
        "\n"
        "{% endif %}\n"
        "\n"
        "{% endnote %}\n"
    )
    assert_stable(text)

