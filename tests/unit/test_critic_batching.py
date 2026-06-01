"""Critic batching on real-sized fixtures."""

from __future__ import annotations

from pathlib import Path

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.chunker import chunk_segments
from ydbdoc_review.segmentation.extractor import extract_segments

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures/markdown_files/ru/core/reference/ydb-cli/parameterized-query-execution.md"
)


def test_parameterized_query_splits_into_multiple_critic_batches():
    ru = _FIXTURE.read_text(encoding="utf-8")
    segments = extract_segments(parse_markdown(ru))
    batches = chunk_segments(segments, max_chars=4000)
    assert len(segments) > 50
    assert len(batches) >= 5
    for batch in batches:
        if len(batch.segments) > 1:
            assert batch.total_chars <= 4000
