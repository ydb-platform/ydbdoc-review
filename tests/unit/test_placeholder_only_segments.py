"""§6.172: placeholder-only segments must not grow prose (#48785)."""

from __future__ import annotations

import pytest

from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.translator import validate_segment_translation
from ydbdoc_review.validation.markers import is_placeholder_only_text


def _seg(text: str, *, seg_id: str = "s0001") -> Segment:
    return Segment(
        id=seg_id,
        kind=SegmentKind.TABLE_BODY_CELL,
        path=["table:row1:col1"],
        text=text,
        placeholders=[],
        ast_path=[0],
    )


def test_is_placeholder_only_text():
    assert is_placeholder_only_text("⟦C1⟧")
    assert is_placeholder_only_text("  ⟦C1⟧  ")
    assert is_placeholder_only_text("⟦C1⟧⟦U1⟧")
    assert not is_placeholder_only_text("⟦C1⟧ section")
    assert not is_placeholder_only_text("plain")
    assert not is_placeholder_only_text("")


def test_validate_rejects_prose_around_placeholder_only_key():
    """#48785: LLM expanded `default_group` key cell into a sentence."""
    src = _seg("⟦C1⟧")
    with pytest.raises(TranslationValidationError, match="placeholder-only"):
        validate_segment_translation(
            src,
            "SID assigned when no settings in the ⟦C1⟧ section.",
        )


def test_validate_accepts_marker_only_copy():
    validate_segment_translation(_seg("⟦C1⟧"), "⟦C1⟧")
