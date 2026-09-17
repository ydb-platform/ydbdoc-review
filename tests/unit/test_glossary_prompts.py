"""Tests for glossary-specific prompt selection."""

from __future__ import annotations

from ydbdoc_review.segmentation.chunker import Batch
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.prompts import (
    build_critic_batch_messages,
    build_translate_messages,
)


def _one_segment_batch() -> Batch:
    seg = Segment(
        id="s0001",
        kind=SegmentKind.PARAGRAPH,
        path=["Cluster"],
        text="**Кластер** или **cluster** …",
        placeholders=[],
        ast_path=[0],
    )
    return Batch(index=0, segments=[seg])


def test_glossary_file_uses_glossary_templates():
    glossary = load_glossary()
    batch = _one_segment_batch()
    path = "ydb/docs/ru/core/concepts/glossary.md"

    translate = build_translate_messages(
        batch,
        glossary,
        file_path=path,
        source_lang="ru",
        target_lang="en",
    )
    assert "glossary profile" in translate[1]["content"]
    assert "Glossary term format" in translate[0]["content"]

    critic = build_critic_batch_messages(
        batch,
        {"s0001": "**cluster** …"},
        glossary,
        file_path=path,
        batch_count=1,
    )
    assert "**glossary**" in critic[1]["content"].lower()
    assert "Glossary term format" in critic[0]["content"]


def test_default_file_uses_standard_templates():
    glossary = load_glossary()
    batch = _one_segment_batch()
    path = "ydb/docs/ru/core/concepts/query_execution/index.md"

    translate = build_translate_messages(
        batch,
        glossary,
        file_path=path,
        source_lang="ru",
        target_lang="en",
    )
    assert "glossary profile" not in translate[1]["content"]
    assert "Glossary term format" not in translate[0]["content"]
