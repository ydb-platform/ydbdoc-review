"""Tests for YAML front matter helpers."""

from __future__ import annotations

from ydbdoc_review.parsing.front_matter import (
    apply_front_matter_updates,
    dump_front_matter,
    parse_front_matter,
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
