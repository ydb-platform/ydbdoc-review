"""Tests for semantic placeholder role validation."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.ast_types import InlineLink, InlineVariable
from ydbdoc_review.segmentation.types import ProtectedInline, Segment, SegmentKind
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.translator import validate_segment_translation
from ydbdoc_review.validation.placeholder_roles import placeholder_roles_valid


def _seg(text: str, placeholders: list[ProtectedInline]) -> Segment:
    return Segment(
        id="t1",
        kind=SegmentKind.PARAGRAPH,
        path=[],
        text=text,
        placeholders=placeholders,
        ast_path=[],
    )


def test_roles_reject_variable_in_link_destination():
    seg = _seg(
        "on ⟦V1⟧ server [auth](⟦U1⟧).",
        [
            ProtectedInline(
                placeholder="⟦V1⟧",
                node=InlineVariable(name="ydb-short-name", raw="{{ ydb-short-name }}"),
            ),
            ProtectedInline(
                placeholder="⟦U1⟧",
                node=InlineLink(href="../../security/authentication.md", children=[]),
            ),
        ],
    )
    bad = "Used if [auth](⟦V1⟧) on ⟦U1⟧ server."
    assert not placeholder_roles_valid(seg, bad)
    with pytest.raises(TranslationValidationError, match="placeholder role mismatch"):
        validate_segment_translation(seg, bad)


def test_roles_accept_correct_placement():
    seg = _seg(
        "on ⟦V1⟧ server [auth](⟦U1⟧).",
        [
            ProtectedInline(
                placeholder="⟦V1⟧",
                node=InlineVariable(name="ydb-short-name", raw="{{ ydb-short-name }}"),
            ),
            ProtectedInline(
                placeholder="⟦U1⟧",
                node=InlineLink(href="../../security/authentication.md", children=[]),
            ),
        ],
    )
    good = "on ⟦V1⟧ server. Used if [auth](⟦U1⟧)."
    assert placeholder_roles_valid(seg, good)
    validate_segment_translation(seg, good)
