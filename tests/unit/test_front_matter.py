"""Tests for YAML front matter helpers."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.front_matter import (
    FrontMatterValueRecord,
    _encode_front_matter_value,
    apply_front_matter_updates,
    dump_front_matter,
    parse_front_matter,
    parse_front_matter_with_spans,
    translatable_front_matter_fields,
)
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.reinsert import reinsert_segments


def test_parse_and_preserve_non_translatable_keys():
    raw = "title: Заголовок\nvcsPath: ru/path.md\neditable: false\ndescription: Описание\n"
    fields = parse_front_matter(raw)
    assert fields["vcsPath"] == "ru/path.md"
    assert fields["editable"] is False

    updated = apply_front_matter_updates(
        raw,
        {"title": "Title", "description": "Description"},
    )
    out = parse_front_matter(updated)
    assert out["title"] == "Title"
    assert out["description"] == "Description"
    assert out["vcsPath"] == "ru/path.md"
    assert out["editable"] is False


def test_translatable_fields_only_title_and_description():
    raw = "title: T\nvcsPath: x\n"
    assert translatable_front_matter_fields(raw) == {"title": "T"}


def test_front_matter_segment_round_trip():
    text = (
        "---\n"
        "title: RU title\n"
        "vcsPath: docs/ru/a.md\n"
        "description: RU desc\n"
        "---\n\n"
        "# Body\n"
    )
    doc = parse_markdown(text)
    segments = extract_segments(doc)
    fm = [s for s in segments if s.kind.value == "front_matter"]
    assert len(fm) == 2
    assert fm[0].text == "RU title"
    assert {s.text for s in fm} == {"RU title", "RU desc"}

    translations = {s.id: s.text.replace("RU", "EN") for s in segments}
    reinsert_segments(doc, segments, translations)
    out = render_markdown(doc)
    assert "title: EN title" in out
    assert "description: EN desc" in out
    assert "vcsPath: docs/ru/a.md" in out


def test_dump_front_matter_key_order():
    fields = {"title": "T", "vcsPath": "p", "description": "D"}
    body = dump_front_matter(fields, key_order=["title", "vcsPath", "description"])
    assert body.index("title:") < body.index("vcsPath:") < body.index("description:")


@pytest.mark.parametrize(
    ("raw", "expected_value"),
    [
        ("title: Привет # keep\nx: 1\n", "A longer English title"),
        ("title: 'Привет' # keep\nx: 1\n", "A longer English title"),
        ('title: "Привет" # keep\nx: 1\n', "A longer English title"),
        ("title: | # keep\n  Привет\nx: 1\n", "A longer English title\n"),
        ("title: > # keep\n  Привет\nx: 1\n", "A longer English title\n"),
    ],
)
def test_style_preserving_different_length_update(raw, expected_value):
    updated = apply_front_matter_updates(raw, {"title": "A longer English title"})
    assert parse_front_matter(updated)["title"] == expected_value
    assert "# keep" in updated
    assert "x: 1" in updated
    assert updated.startswith("title: ")
    if raw[7] in "'\"|>":
        assert updated[7] == raw[7]


@pytest.mark.parametrize(
    "raw",
    [
        "description: Привет # keep\nx: 1\n",
        "description: 'Привет' # keep\nx: 1\n",
        'description: "Привет" # keep\nx: 1\n',
        "description: | # keep\n  Привет\nx: 1\n",
        "description: > # keep\n  Привет\nx: 1\n",
    ],
)
def test_description_style_preserving_different_length_update(raw):
    updated = apply_front_matter_updates(raw, {"description": "A longer English description"})
    value = parse_front_matter(updated)["description"]
    assert value.startswith("A longer English description")
    assert "# keep" in updated
    assert "x: 1" in updated


def test_alias_backed_selected_value_is_rejected():
    with pytest.raises(ValueError, match="source_map_invalid_front_matter:title"):
        parse_front_matter_with_spans("x: &a Hello\ntitle: *a\n")


def test_empty_non_string_and_unselected_values_have_no_spans():
    _fields, records = parse_front_matter_with_spans(
        "title: ''\ndescription: [a]\nx: text\n"
    )
    assert records == ()


def test_impossible_folded_multiline_update_fails_closed():
    with pytest.raises(ValueError, match="front_matter_translation_requires_style_change:title"):
        apply_front_matter_updates("title: >-\n  one\n", {"title": "line one\nline two"})


def test_encode_rejects_unknown_style():
    record = FrontMatterValueRecord("title", "!", "x", 0, 1, None, b"", b"")
    with pytest.raises(ValueError, match="front_matter_translation_requires_style_change:title"):
        _encode_front_matter_value(record, "y")


def test_strip_block_scalar_preserves_absent_final_newline():
    updated = apply_front_matter_updates("title: |-\n  Привет", {"title": "Hello"})
    assert updated == "title: |-\n  Hello"
    assert parse_front_matter(updated)["title"] == "Hello"


def test_strip_block_scalar_keeps_structural_newline_before_next_key():
    updated = apply_front_matter_updates("title: |-\n  Привет\nx: 1\n", {"title": "Hello"})
    assert updated == "title: |-\n  Hello\nx: 1\n"


def test_block_header_chomping_mutation_fails_closed():
    source = "title: |\n  Привет\n"
    updated = apply_front_matter_updates(source, {"title": "Hello"})
    assert updated.startswith("title: |\n")
    assert "title: |-" not in updated
    assert "title: |+" not in updated
    # Surgical encode must not rewrite the protected header bytes.
    assert updated.encode().startswith(b"title: |\n")
