"""Tests for segment chunker."""

from __future__ import annotations

from pathlib import Path

import pytest

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.chunker import Batch, chunk_segments
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.types import ProtectedInline, Segment, SegmentKind


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


def test_chunker_on_real_fixtures():
    """Chunker must produce valid batches on every real fixture."""
    fixtures = Path(__file__).parent.parent / "fixtures" / "markdown_files"
    files = list(fixtures.rglob("*.md"))
    assert files

    for f in files:
        text = f.read_text(encoding="utf-8")
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
    """Sanity: typical files should produce a small number of batches."""
    fixtures = Path(__file__).parent.parent / "fixtures" / "markdown_files"
    files = list(fixtures.rglob("*.md"))

    stats: dict[str, int] = {}
    for f in files:
        text = f.read_text(encoding="utf-8")
        doc = parse_markdown(text)
        segs = extract_segments(doc)
        batches = chunk_segments(segs, max_chars=4000)
        stats[f.name] = len(batches)

    # No file should explode into 100+ batches with default settings.
    for name, n in stats.items():
        assert n < 100, f"{name}: too many batches ({n})"

