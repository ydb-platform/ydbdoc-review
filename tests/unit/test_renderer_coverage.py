"""Additional renderer tests for uncovered branches."""

from __future__ import annotations

from ydbdoc_review.parsing.ast_types import (
    Document,
    InlineText,
    YfmTab,
    YfmTabs,
)
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown


def round_trip(text: str) -> str:
    return render_markdown(parse_markdown(text))


def test_front_matter_render():
    text = "---\ntitle: Hello\n---\n\nBody.\n"
    out = round_trip(text)
    assert out.startswith("---\n")
    assert "title: Hello" in out
    assert "Body." in out


def test_empty_document_adds_newline():
    assert render_markdown(Document(children=[])) == "\n"


def test_empty_yfm_tabs():
    doc = Document(children=[YfmTabs(variant="tabs", children=[])])
    out = render_markdown(doc)
    assert "{% list tabs %}" in out
    assert "{% endlist %}" in out


def test_yfm_tab_title_only():
    doc = Document(
        children=[
            YfmTabs(
                variant="tabs",
                children=[YfmTab(title=[InlineText(content="Only title")], children=[])],
            )
        ]
    )
    out = render_markdown(doc)
    assert "- Only title" in out


def test_table_column_alignments():
    text = (
        "| Left | Center | Right |\n"
        "| :--- | :---: | ---: |\n"
        "| a | b | c |\n"
    )
    out = round_trip(text)
    assert "| :--- |" in out
    assert "| :---: |" in out
    assert "| ---: |" in out


def test_indented_code_block():
    text = "    line one\n    line two\n"
    out = round_trip(text)
    assert "    line one" in out
    assert "    line two" in out


def test_thematic_break_variants():
    assert "***" in round_trip("before\n\n***\n\nafter\n")
    assert "___" in round_trip("before\n\n___\n\nafter\n")


def test_loose_bullet_list_blank_line_between_items():
    text = "- one\n\n- two\n"
    out = round_trip(text)
    assert "- one" in out
    assert "- two" in out


def test_inline_code_with_backticks_inside():
    text = "Use `` ` `` for backtick.\n"
    out = round_trip(text)
    assert "`" in out


def test_hard_line_break():
    text = "line one  \nline two\n"
    out = round_trip(text)
    assert "line one" in out
    assert "line two" in out


def test_fenced_code_without_trailing_newline_in_ast():
    doc = parse_markdown("```\nhello\n```\n")
    block = doc.children[0]
    block.content = "hello"  # strip trailing newline from AST
    out = render_markdown(doc)
    assert "hello" in out


def test_yfm_note_with_title():
    text = '{% note info "Title" %}\n\nBody.\n\n{% endnote %}\n'
    out = round_trip(text)
    assert '"Title"' in out
    assert "Body." in out


def test_yfm_cut_inner_without_trailing_newline():
    text = '{% cut "Title" %}\n\nBody.\n\n{% endcut %}\n'
    out = round_trip(text)
    assert "Body." in out
    assert "{% endcut %}" in out


def test_yfm_if_branch_body_without_trailing_newline():
    text = "{% if oss %}\n\nShort.\n\n{% endif %}\n"
    first = round_trip(text)
    second = round_trip(first)
    assert first == second


def test_image_with_size_and_title():
    text = '![alt](img.png =100x200 "title")\n'
    out = round_trip(text)
    assert "=100x200" in out
