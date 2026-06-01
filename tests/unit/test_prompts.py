"""Tests for prompt template loading and message builders."""

from __future__ import annotations

import json

from ydbdoc_review.segmentation.chunker import Batch
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.prompts import (
    build_analyze_messages,
    build_critic_batch_messages,
    build_critic_messages,
    build_translate_messages,
    build_verify_batch_messages,
    build_verify_messages,
    load_template,
    render_template,
    segments_to_batch_json,
    segments_to_critic_batch_json,
)


def _segment(seg_id: str, text: str) -> Segment:
    return Segment(
        id=seg_id,
        kind=SegmentKind.PARAGRAPH,
        path=["Intro"],
        text=text,
        placeholders=[],
        ast_path=[0],
    )


def test_load_template_v1():
    text = load_template("translate")
    assert "Translate the following document segments" in text
    assert "{batch_json}" in text


def test_render_template_leaves_unknown_braces():
    out = render_template("Hello {name}, json: {\"a\": 1}", {"name": "World"})
    assert out == 'Hello World, json: {"a": 1}'


def test_segments_to_batch_json():
    seg = _segment("s0001", "Привет ⟦C1⟧")
    payload = json.loads(segments_to_batch_json([seg]))
    assert payload["segments"][0]["id"] == "s0001"
    assert payload["segments"][0]["text"] == "Привет ⟦C1⟧"


def test_build_translate_messages_includes_glossary_and_batch():
    glossary = load_glossary()
    batch = Batch(index=0, segments=[_segment("s0001", "Текст")])
    messages = build_translate_messages(
        batch, glossary, file_path="docs/ru/foo.md"
    )
    assert len(messages) == 2
    system = messages[0]["content"]
    user = messages[1]["content"]
    assert isinstance(system, str) and "GLOSSARY:" in system
    assert "параметризованный запрос" in system or "YDB" in system
    assert isinstance(user, str)
    assert "docs/ru/foo.md" in user
    assert '"id": "s0001"' in user
    assert "English style" in user


def test_build_translate_messages_skips_style_guide_for_ru_target():
    glossary = load_glossary()
    batch = Batch(index=0, segments=[_segment("s0001", "Text")])
    messages = build_translate_messages(
        batch,
        glossary,
        file_path="docs/en/foo.md",
        source_lang="en",
        target_lang="ru",
    )
    user = messages[1]["content"]
    assert isinstance(user, str)
    assert "English style" not in user


def test_build_critic_messages():
    glossary = load_glossary()
    seg = _segment("s0001", "x")
    messages = build_critic_messages(
        source_text="RU body",
        translated_text="EN body",
        segments=[seg],
        glossary=glossary,
        file_path="docs/ru/bar.md",
    )
    user = messages[1]["content"]
    assert isinstance(user, str)
    assert "RU body" in user
    assert "EN body" in user
    assert '"segment_id"' in user


def test_build_analyze_messages():
    glossary = load_glossary()
    pairs = [{"ru_path": "a.md", "en_path": "b.md", "ru_text": "x", "en_text": None}]
    messages = build_analyze_messages(pairs, glossary)
    user = messages[1]["content"]
    assert isinstance(user, str)
    assert '"pairs"' in user
    assert "a.md" in user


def test_build_verify_messages():
    glossary = load_glossary()
    seg = _segment("s0001", "x")
    messages = build_verify_messages(
        source_text="RU",
        translated_text="EN",
        segments=[seg],
        prior_issues=[{"segment_id": "s0001", "severity": "warning", "category": "x", "comment": "y"}],
        glossary=glossary,
        file_path="docs/ru/foo.md",
    )
    user = messages[1]["content"]
    assert isinstance(user, str)
    assert "Previously reported issues" in user
    assert "s0001" in user


def test_segments_to_critic_batch_json():
    seg = _segment("s0001", "RU text")
    payload = json.loads(
        segments_to_critic_batch_json([seg], {"s0001": "EN text"})
    )
    row = payload["segments"][0]
    assert row["source_text"] == "RU text"
    assert row["translated_text"] == "EN text"


def test_build_critic_batch_messages():
    glossary = load_glossary()
    batch = Batch(index=0, segments=[_segment("s0001", "RU")])
    messages = build_critic_batch_messages(
        batch,
        {"s0001": "EN"},
        glossary,
        file_path="docs/ru/foo.md",
        batch_count=3,
    )
    user = messages[1]["content"]
    assert isinstance(user, str)
    assert "Batch: 1 of 3" in user
    assert '"source_text": "RU"' in user
    assert '"translated_text": "EN"' in user
    assert "RU body" not in user


def test_build_verify_batch_messages():
    glossary = load_glossary()
    batch = Batch(index=1, segments=[_segment("s0002", "RU")])
    messages = build_verify_batch_messages(
        batch,
        {"s0002": "EN"},
        [{"segment_id": "s0002", "severity": "warning", "category": "x", "comment": "y"}],
        glossary,
        file_path="docs/ru/foo.md",
        batch_count=2,
    )
    user = messages[1]["content"]
    assert isinstance(user, str)
    assert "Batch: 2 of 2" in user
    assert "Previously reported issues" in user
