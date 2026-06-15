"""Filter spurious critic issues about ``⟦V⟧`` / ``{{ ydb-short-name }}`` drift."""

from __future__ import annotations

import logging

from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse, CriticVerdict
from ydbdoc_review.validation.markers import variable_placeholder_drift_only

logger = logging.getLogger(__name__)


def is_spurious_variable_placeholder_issue(
    issue: CriticIssueOut,
    segment: Segment | None,
    translation: str | None,
) -> bool:
    """Drop critic noise when only ``{{ ydb-short-name }}`` placement/count drifts."""
    if segment is None or translation is None or not issue.segment_id:
        return False
    cat = issue.category.lower().replace("_", " ")
    if "placeholder" not in cat:
        return False
    return variable_placeholder_drift_only(segment.text, translation)


def drop_spurious_placeholder_issues(
    issues: list[CriticIssueOut],
    segments: list[Segment],
    translations: dict[str, str],
) -> list[CriticIssueOut]:
    by_id = {s.id: s for s in segments}
    out: list[CriticIssueOut] = []
    for issue in issues:
        seg = by_id.get(issue.segment_id) if issue.segment_id else None
        trans = translations.get(issue.segment_id) if issue.segment_id else None
        if is_spurious_variable_placeholder_issue(issue, seg, trans):
            logger.info(
                "Ignoring spurious placeholder critic issue for %s",
                issue.segment_id,
            )
            continue
        out.append(issue)
    return out


def _verdict_from_issues(issues: list[CriticIssueOut]) -> CriticVerdict:
    if not issues:
        return "ok"
    if any(i.severity == "blocked" for i in issues):
        return "blocked"
    return "warnings"


def filter_critic_response(
    response: CriticResponse | None,
    segments: list[Segment],
    translations: dict[str, str],
) -> CriticResponse | None:
    """Remove spurious placeholder issues and refresh verdict."""
    if response is None:
        return None
    filtered = drop_spurious_placeholder_issues(response.issues, segments, translations)
    if filtered == response.issues:
        return response
    return CriticResponse(verdict=_verdict_from_issues(filtered), issues=filtered)
