"""Segment-level fence parity validation."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.translator import validate_segment_translation


def test_fence_count_mismatch_raises():
    seg = Segment(
        id="t1",
        kind=SegmentKind.PARAGRAPH,
        path=[],
        text="Run:\n\n```\na\n```\n",
        placeholders=[],
        ast_path=[],
    )
    with pytest.raises(TranslationValidationError, match="fence count mismatch"):
        validate_segment_translation(
            seg,
            "Run:\n\n```\na\n```\n\n```\nextra\n```\n",
        )


def test_fence_count_match_ok():
    seg = Segment(
        id="t1",
        kind=SegmentKind.PARAGRAPH,
        path=[],
        text="Run:\n\n```\na\n```\n",
        placeholders=[],
        ast_path=[],
    )
    validate_segment_translation(seg, "Run EN:\n\n```\na\n```\n")
