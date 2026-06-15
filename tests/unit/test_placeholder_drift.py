"""Tests for spurious placeholder drift filtering."""

from __future__ import annotations

from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse
from ydbdoc_review.validation.markers import variable_placeholder_drift_only
from ydbdoc_review.validation.placeholder_drift import (
    drop_spurious_placeholder_issues,
    filter_critic_response,
)


def _segment(seg_id: str, text: str) -> Segment:
    return Segment(
        id=seg_id,
        kind=SegmentKind.PARAGRAPH,
        path=[],
        text=text,
        placeholders=[],
        ast_path=[0],
    )


def test_variable_placeholder_drift_only_allows_one_missing_v():
    ru = (
        "⟦V1⟧ text ⟦V2⟧ more [link](⟦U1⟧) ⟦V3⟧ topics ⟦V4⟧ end"
    )
    en = (
        "⟦V1⟧ text ⟦V2⟧ more [link](⟦U1⟧) ⟦V3⟧ end"
    )
    assert variable_placeholder_drift_only(ru, en)


def test_variable_placeholder_drift_rejects_code_mismatch():
    ru = "Use ⟦C1⟧ and ⟦V1⟧"
    en = "Use ⟦C2⟧ and ⟦V1⟧"
    assert not variable_placeholder_drift_only(ru, en)


def test_drop_spurious_placeholder_issues_streaming_query_style():
    """Regression #41206: critic placeholder corruption on {{ ydb-short-name }} drift."""
    ru = (
        "⟦V1⟧ реализует потоковую обработку. Интеграция в ⟦V2⟧ позволяет получать "
        "данные из [топиков](⟦U1⟧) ⟦V3⟧, записывать в ⟦V4⟧."
    )
    en = (
        "⟦V1⟧ implements stream processing. Integration into ⟦V2⟧ lets you ingest "
        "data from [topics](⟦U1⟧), write inside ⟦V3⟧."
    )
    seg = _segment("s0004", ru)
    issue = CriticIssueOut(
        segment_id="s0004",
        severity="warning",
        category="placeholder corruption",
        comment="Missing placeholder ⟦V4⟧",
        suggested_text="broken",
    )
    filtered = drop_spurious_placeholder_issues([issue], [seg], {"s0004": en})
    assert filtered == []


def test_filter_critic_response_clears_verdict():
    ru = "⟦V1⟧ one ⟦V2⟧ two"
    en = "⟦V1⟧ one two"
    seg = _segment("s1", ru)
    response = CriticResponse(
        verdict="warnings",
        issues=[
            CriticIssueOut(
                segment_id="s1",
                severity="warning",
                category="placeholder corruption",
                comment="drift",
                suggested_text=None,
            )
        ],
    )
    out = filter_critic_response(response, [seg], {"s1": en})
    assert out is not None
    assert out.issues == []
    assert out.verdict == "ok"
