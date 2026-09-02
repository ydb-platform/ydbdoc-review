"""Tests for segment chunker."""

from __future__ import annotations

from pathlib import Path

import pytest

from ydbdoc_review.parsing.ast_types import AmbiguousYfmStructureError
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.chunker import Batch, chunk_segments
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.types import Segment, SegmentKind


def make_seg(idx: int, text: str) -> Segment:
    return Segment(
        id=f"s{idx:04d}",
        kind=SegmentKind.PARAGRAPH,
        path=[],
        text=text,
        placeholders=[],
        ast_path=[idx],
    )


# --- Basics ---


def test_empty_input():
    assert chunk_segments([], max_chars=100) == []


def test_invalid_budget():
    with pytest.raises(ValueError):
        chunk_segments([], max_chars=0)


def test_single_segment_within_budget():
    segs = [make_seg(1, "hello")]
    batches = chunk_segments(segs, max_chars=100)
    assert len(batches) == 1
    assert batches[0].index == 0
    assert batches[0].segments == segs


def test_multiple_small_segments_in_one_batch():
    segs = [make_seg(i, "x" * 10) for i in range(5)]
    batches = chunk_segments(segs, max_chars=100)
    assert len(batches) == 1
    assert len(batches[0].segments) == 5



def test_packing_splits_when_budget_exceeded():
    # Each segment 30 chars, budget 100 → 3 fit per batch.
    segs = [make_seg(i, "x" * 30) for i in range(7)]
    batches = chunk_segments(segs, max_chars=100)
    # Expected sizes: 90, 90, 30 → 3 batches.
    assert len(batches) == 3
    assert [len(b.segments) for b in batches] == [3, 3, 1]


def test_oversized_segment_is_own_batch():
    segs = [
        make_seg(1, "small"),
        make_seg(2, "x" * 500),  # larger than budget
        make_seg(3, "small"),
    ]
    batches = chunk_segments(segs, max_chars=100)
    assert len(batches) == 3
    assert batches[0].segments == [segs[0]]
    assert batches[1].segments == [segs[1]]
    assert batches[2].segments == [segs[2]]


def test_oversized_in_middle_does_not_merge_with_neighbors():
    segs = [
        make_seg(1, "a" * 40),
        make_seg(2, "b" * 40),
        make_seg(3, "X" * 500),
        make_seg(4, "c" * 40),
    ]
    batches = chunk_segments(segs, max_chars=100)
    # First two pack together (80), oversized alone, last alone.
    assert [len(b.segments) for b in batches] == [2, 1, 1]


def test_batch_indices_are_sequential():
    segs = [make_seg(i, "x" * 30) for i in range(10)]
    batches = chunk_segments(segs, max_chars=100)
    for i, b in enumerate(batches):
        assert b.index == i


def test_segment_order_preserved():
    segs = [make_seg(i, "x" * 30) for i in range(10)]
    batches = chunk_segments(segs, max_chars=100)
    flat = [s for b in batches for s in b.segments]
    assert flat == segs


def test_no_segment_lost_or_duplicated():
    segs = [make_seg(i, f"text-{i}") for i in range(20)]
    batches = chunk_segments(segs, max_chars=50)
    flat_ids = [s.id for b in batches for s in b.segments]
    assert flat_ids == [s.id for s in segs]
    assert len(set(flat_ids)) == len(segs)


def test_total_chars_property():
    segs = [make_seg(i, "x" * 30) for i in range(3)]
    batch = Batch(index=0, segments=segs)
    assert batch.total_chars == 90


# --- Real fixtures ---


EXPECTED_AMBIGUOUS_YFM_FILES = {"en/core/reference/ydb-sdk/topic.md"}


def test_expected_ambiguous_yfm_file_set_is_exact():
    assert EXPECTED_AMBIGUOUS_YFM_FILES == {"en/core/reference/ydb-sdk/topic.md"}


def test_chunker_on_real_fixtures():
    """Chunker must produce valid batches on every real fixture."""
    fixtures = Path(__file__).parent.parent / "fixtures" / "markdown_files"
    files = list(fixtures.rglob("*.md"))
    assert files

    for f in files:
        text = f.read_text(encoding="utf-8")
        relative_path = f.relative_to(fixtures).as_posix()
        if relative_path in EXPECTED_AMBIGUOUS_YFM_FILES:
            with pytest.raises(AmbiguousYfmStructureError, match="unowned direct-depth"):
                parse_markdown(text)
            continue
        doc = parse_markdown(text)
        segs = extract_segments(doc)
        batches = chunk_segments(segs, max_chars=4000)

        # Invariants:
        # 1. Every segment appears exactly once across all batches.
        flat_ids = [s.id for b in batches for s in b.segments]
        assert flat_ids == [s.id for s in segs], (
            f"{f.name}: segments reordered or lost"
        )
        # 2. No batch is empty.
        for b in batches:
            assert b.segments, f"{f.name}: empty batch {b.index}"
        # 3. A batch with >1 segment fits within budget.
        for b in batches:
            if len(b.segments) > 1:
                assert b.total_chars <= 4000, (
                    f"{f.name}: batch {b.index} exceeds budget "
                    f"({b.total_chars} chars)"
                )


def test_chunker_reasonable_batch_count_on_real_files():
    """Every real-fixture batch boundary is required by greedy packing."""
    fixtures = Path(__file__).parent.parent / "fixtures" / "markdown_files"
    files = list(fixtures.rglob("*.md"))

    for f in files:
        text = f.read_text(encoding="utf-8")
        relative_path = f.relative_to(fixtures).as_posix()
        if relative_path in EXPECTED_AMBIGUOUS_YFM_FILES:
            with pytest.raises(AmbiguousYfmStructureError, match="unowned direct-depth"):
                parse_markdown(text)
            continue
        doc = parse_markdown(text)
        segs = extract_segments(doc)
        batches = chunk_segments(segs, max_chars=4000)

        for batch_index, batch in enumerate(batches[:-1]):
            next_batch = batches[batch_index + 1]
            segment = batch.segments[0]
            next_segment = next_batch.segments[0]
            dense_singleton = (
                len(batch.segments) == 1 and len(segment.placeholders) >= 8
            )
            oversized_singleton = (
                len(batch.segments) == 1 and len(segment.text) > 4000
            )
            next_segment_is_dense = len(next_segment.placeholders) >= 8
            next_segment_exceeds_budget = (
                batch.total_chars + len(next_segment.text) > 4000
            )
            assert (
                dense_singleton
                or oversized_singleton
                or next_segment_is_dense
                or next_segment_exceeds_budget
            ), f"{f.name}: unnecessary batch boundary after {batch.index}"
