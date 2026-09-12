"""Tests for YAML front matter helpers (REQUIREMENTS_RU.md §5 / §9 / §14)."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.front_matter import (
    FrontMatterError,
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
    ("raw", "updates", "protected_fragments"),
    [
        (
            "title: Заголовок\n# keep comment\nvcsPath: ru/path.md\ndescription: Описание\n",
            {"title": "Title", "description": "Description"},
            ["# keep comment", "vcsPath: ru/path.md"],
        ),
        (
            "title: 'Заголовок'\nvcsPath: \"ru/path.md\"\ndescription: \"Описание\"\n",
            {"title": "A longer Title", "description": "Desc"},
            ["title: 'A longer Title'", 'vcsPath: "ru/path.md"', 'description: "Desc"'],
        ),
        (
            'title: "Заголовок"\ndescription: \'Описание\'\neditable: false\n',
            {"title": "Title", "description": "Description"},
            ['title: "Title"', "description: 'Description'", "editable: false"],
        ),
        (
            "title: |\n  Привет\n  мир\nvcsPath: x\n",
            {"title": "Hello\nworld\n"},
            ["title: |", "vcsPath: x"],
        ),
        (
            "title: |-\n  Привет\n  мир\nvcsPath: x\n",
            {"title": "Hello\nworld"},
            ["title: |-", "vcsPath: x"],
        ),
        (
            "title: |+\n  Привет\n  мир\n\nvcsPath: x\n",
            {"title": "Hello\nworld\n\n"},
            ["title: |+", "vcsPath: x"],
        ),
        (
            "title: >\n  Привет мир\nvcsPath: x\n",
            {"title": "Hello world\n"},
            ["title: >", "vcsPath: x"],
        ),
        (
            "title: >-\n  Привет мир\nvcsPath: x\n",
            {"title": "Hello world"},
            ["title: >-", "vcsPath: x"],
        ),
    ],
)
def test_apply_updates_preserves_style_comments_and_unselected(
    raw: str,
    updates: dict[str, str],
    protected_fragments: list[str],
):
    out = apply_front_matter_updates(raw, updates)
    fields = parse_front_matter(out)
    for key, value in updates.items():
        assert fields[key] == value
    for fragment in protected_fragments:
        assert fragment in out

    # Unselected keys stay semantically identical.
    source_fields, _ = parse_front_matter_with_spans(raw)
    for key, value in source_fields.items():
        if key in updates:
            continue
        assert fields[key] == value


def test_apply_updates_preserves_unselected_bytes_outside_value_spans():
    raw = (
        "title: 'Заголовок'\n"
        "# keep me\n"
        "vcsPath: \"ru/path.md\"\n"
        "description: \"Описание\"\n"
        "editable: false\n"
    )
    out = apply_front_matter_updates(
        raw, {"title": "Title", "description": "Description"}
    )
    # Everything outside the two value interiors must be byte-identical.
    assert out == (
        "title: 'Title'\n"
        "# keep me\n"
        "vcsPath: \"ru/path.md\"\n"
        "description: \"Description\"\n"
        "editable: false\n"
    )


def test_alias_backed_title_is_not_translatable():
    raw = "x: &a Hello\ntitle: *a\ndescription: Описание\n"
    assert translatable_front_matter_fields(raw) == {"description": "Описание"}
    out = apply_front_matter_updates(raw, {"description": "Description"})
    assert "title: *a" in out
    assert "x: &a Hello" in out
    assert parse_front_matter(out)["description"] == "Description"


def test_duplicate_selected_key_is_not_surgically_translatable():
    raw = "title: One\ntitle: Two\ndescription: D\n"
    assert "title" not in translatable_front_matter_fields(raw)
    assert translatable_front_matter_fields(raw) == {"description": "D"}


def test_empty_and_non_string_selected_values_are_not_translatable():
    raw = "title: ''\ndescription:\nvcsPath: x\nmeta: [1, 2]\n"
    assert translatable_front_matter_fields(raw) == {}


def test_folded_interior_newlines_fail_closed():
    raw = "title: >-\n  one line\n"
    with pytest.raises(FrontMatterError, match="title"):
        apply_front_matter_updates(raw, {"title": "line1\nline2"})
