"""Tests for segment extractor and inline protector."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.inline_protector import (
    protect_inline,
    restore_inline_text,
)
from ydbdoc_review.segmentation.types import SegmentKind


# --- Inline protector unit tests ---


def test_protect_plain_text_no_placeholders():
    doc = parse_markdown("Hello world.\n")
    para = doc.children[0]
    text, placeholders = protect_inline(para.children)
    assert text == "Hello world."
    assert placeholders == []


def test_protect_code_yields_marker():
    doc = parse_markdown("Use `--yaml` flag.\n")
    para = doc.children[0]
    text, placeholders = protect_inline(para.children)
    assert text == "Use ⟦C1⟧ flag."
    assert len(placeholders) == 1
    assert placeholders[0].placeholder == "⟦C1⟧"


def test_protect_link_yields_marker():
    doc = parse_markdown("See [docs](http://x) for details.\n")
    para = doc.children[0]
    text, placeholders = protect_inline(para.children)
    assert text == "See ⟦L1⟧ for details."
    assert placeholders[0].placeholder == "⟦L1⟧"


def test_protect_variable_yields_marker():
    doc = parse_markdown("Use {{ ydb-short-name }} CLI.\n")
    para = doc.children[0]
    text, placeholders = protect_inline(para.children)
    assert text == "Use ⟦V1⟧ CLI."
    assert placeholders[0].placeholder == "⟦V1⟧"


def test_protect_multiple_mixed():
    doc = parse_markdown(
        "Run `--input-file` with [docs]({{ url-var }}) and {{ var }}.\n"
    )
    para = doc.children[0]
    text, placeholders = protect_inline(para.children)
    # Order: code, link, variable.
    assert "⟦C1⟧" in text
    assert "⟦L1⟧" in text
    assert "⟦V1⟧" in text
    assert len(placeholders) == 3


def test_protect_emphasis_kept_inline():
    doc = parse_markdown("This is *italic* text.\n")
    para = doc.children[0]
    text, placeholders = protect_inline(para.children)
    # Emphasis markers stay; no placeholders.
    assert text == "This is *italic* text."
    assert placeholders == []


def test_protect_strong_with_nested_code():
    doc = parse_markdown("Use **bold `code` here**.\n")
    para = doc.children[0]
    text, placeholders = protect_inline(para.children)
    assert text == "Use **bold ⟦C1⟧ here**."
    assert len(placeholders) == 1


def test_restore_identity_roundtrip():
    """protect then restore must give back the original rendered text."""
    samples = [
        "Plain text.\n",
        "Use `--yaml` flag.\n",
        "See [docs](http://x).\n",
        "Use {{ name }} CLI.\n",
        "Mix `a` and [b](c) and {{ d }} and **bold** and *em*.\n",
    ]
    for sample in samples:
        doc = parse_markdown(sample)
        para = doc.children[0]
        text, placeholders = protect_inline(para.children)
        restored = restore_inline_text(text, placeholders)
        # Round-trip through render: restored markdown text equals the
        # rendered version of the original paragraph's inline content.
        from ydbdoc_review.rendering.markdown_renderer import _render_inline
        expected = _render_inline(para.children)
        assert restored == expected, (
            f"\nsample={sample!r}\ntext={text!r}\nrestored={restored!r}\nexpected={expected!r}"
        )


# --- Extractor: which kinds appear ---


def test_extract_simple_paragraph():
    doc = parse_markdown("Hello world.\n")
    segments = extract_segments(doc)
    assert len(segments) == 1
    seg = segments[0]
    assert seg.kind == SegmentKind.PARAGRAPH
    assert seg.text == "Hello world."
    assert seg.id == "s0001"


def test_extract_heading():
    doc = parse_markdown("# Title\n\nText.\n")
    segments = extract_segments(doc)
    kinds = [s.kind for s in segments]
    assert SegmentKind.HEADING in kinds
    assert SegmentKind.PARAGRAPH in kinds


def test_extract_list_items_separately():
    doc = parse_markdown("- one\n- two\n- three\n")
    segments = extract_segments(doc)
    assert len(segments) == 3
    for s in segments:
        assert s.kind == SegmentKind.PARAGRAPH  # list_item's first paragraph


def test_extract_table_cells():
    doc = parse_markdown(
        "| A | B |\n"
        "| --- | --- |\n"
        "| 1 | 2 |\n"
        "| 3 | 4 |\n"
    )
    segments = extract_segments(doc)
    # 2 header cells + 4 body cells = 6
    assert len(segments) == 6
    header_count = sum(1 for s in segments if s.kind == SegmentKind.TABLE_HEADER_CELL)
    body_count = sum(1 for s in segments if s.kind == SegmentKind.TABLE_BODY_CELL)
    assert header_count == 2
    assert body_count == 4


def test_extract_code_block_not_extracted():
    doc = parse_markdown(
        "Intro.\n\n```bash\necho hi\n```\n\nOutro.\n"
    )
    segments = extract_segments(doc)
    # Only intro + outro.
    assert len(segments) == 2
    texts = [s.text for s in segments]
    assert "Intro." in texts
    assert "Outro." in texts


def test_extract_include_not_extracted():
    doc = parse_markdown(
        "Before.\n\n{% include [x](path.md) %}\n\nAfter.\n"
    )
    segments = extract_segments(doc)
    assert len(segments) == 2
    assert all("include" not in s.text for s in segments)


def test_extract_inside_note():
    doc = parse_markdown(
        "{% note warning %}\n\nBe careful with `--force`.\n\n{% endnote %}\n"
    )
    segments = extract_segments(doc)
    assert len(segments) == 1
    seg = segments[0]
    assert "⟦C1⟧" in seg.text
    assert "note:warning" in seg.path


def test_extract_inside_tabs_with_whitelist():
    """A tab titled 'Python' must not produce a TAB_TITLE segment."""
    doc = parse_markdown(
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
    segments = extract_segments(doc)
    kinds = [s.kind for s in segments]
    assert SegmentKind.TAB_TITLE not in kinds
    # Two content paragraphs.
    body_segments = [s for s in segments if s.kind == SegmentKind.PARAGRAPH]
    assert len(body_segments) == 2


def test_extract_tab_title_non_whitelisted():
    """A non-whitelisted tab title is translatable."""
    doc = parse_markdown(
        "{% list tabs %}\n"
        "\n"
        "- Из консоли\n"
        "\n"
        "  Текст 1.\n"
        "\n"
        "- С нашего сервера\n"
        "\n"
        "  Текст 2.\n"
        "\n"
        "{% endlist %}\n"
    )
    segments = extract_segments(doc)
    titles = [s for s in segments if s.kind == SegmentKind.TAB_TITLE]
    assert len(titles) == 2
    assert "Из консоли" in titles[0].text


def test_extract_term_definition():
    doc = parse_markdown(
        "Some [*cluster] here.\n\n[*cluster]: A set of nodes.\n"
    )
    segments = extract_segments(doc)
    kinds = [s.kind for s in segments]
    assert SegmentKind.TERM_DEFINITION in kinds
    td = next(s for s in segments if s.kind == SegmentKind.TERM_DEFINITION)
    assert td.text == "A set of nodes."


def test_extract_skips_whitespace_only():
    """A paragraph containing only whitespace must not become a segment."""
    # markdown-it usually collapses these, but just in case.
    doc = parse_markdown("Real text.\n")
    segments = extract_segments(doc)
    assert all(s.text.strip() for s in segments)


# --- Path / breadcrumbs ---


def test_path_contains_note_kind():
    doc = parse_markdown(
        "{% note info %}\n\nText.\n\n{% endnote %}\n"
    )
    segments = extract_segments(doc)
    assert any("note:info" in s.path for s in segments)


def test_path_contains_table_position():
    doc = parse_markdown(
        "| A | B |\n| --- | --- |\n| x | y |\n"
    )
    segments = extract_segments(doc)
    paths = [".".join(s.path) for s in segments]
    assert any("header" in p for p in paths)
    assert any("row1" in p for p in paths)


def test_path_contains_if_condition():
    doc = parse_markdown(
        "{% if oss %}\n\nOSS text.\n\n{% endif %}\n"
    )
    segments = extract_segments(doc)
    assert any("if:oss" in s.path for s in segments)


# --- Counters and ids ---


def test_segment_ids_are_unique_and_sequential():
    doc = parse_markdown(
        "# Title\n\nP1.\n\nP2.\n\n- a\n- b\n"
    )
    segments = extract_segments(doc)
    ids = [s.id for s in segments]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert ids[0] == "s0001"


# --- Integration on a real fixture ---


def test_extract_on_real_file_runs():
    """Smoke test: extractor must not crash on any real fixture."""
    from pathlib import Path

    fixtures = Path(__file__).parent.parent / "fixtures" / "markdown_files"
    files = list(fixtures.rglob("*.md"))
    assert files, "no fixtures found"

    for f in files[:5]:  # subset for speed
        text = f.read_text(encoding="utf-8")
        doc = parse_markdown(text)
        segs = extract_segments(doc)
        # Must produce *some* segments for non-trivial files.
        if len(text) > 200:
            assert segs, f"no segments for {f}"
        # All segment ids unique.
        ids = [s.id for s in segs]
        assert len(set(ids)) == len(ids)


def test_extract_placeholders_match_kind():
    """Each placeholder's stored node must match its prefix."""
    doc = parse_markdown(
        "Use `code`, [link](http://x), and {{ var }}.\n"
    )
    segments = extract_segments(doc)
    seg = segments[0]
    for p in seg.placeholders:
        prefix = p.placeholder[1]  # ⟦C1⟧ → 'C'
        if prefix == "C":
            assert p.node.kind == "code"
        elif prefix == "L":
            assert p.node.kind == "link"
        elif prefix == "V":
            assert p.node.kind == "yfm_variable"
        else:
            pytest.fail(f"unexpected prefix in {p.placeholder}")

