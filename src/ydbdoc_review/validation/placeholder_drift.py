"""Filter spurious critic issues about placeholder drift across RU/EN."""

from __future__ import annotations

import logging
import re

from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse, CriticVerdict
from ydbdoc_review.validation.markers import (
    cross_lang_placeholder_drift_only,
    variable_placeholder_drift_only,
)

logger = logging.getLogger(__name__)

_PLACEHOLDER_ISSUE = re.compile(r"placeholder", re.IGNORECASE)
_REORDER_ISSUE = re.compile(
    r"order|reorder|renumber|changed to ⟦|mapping",
    re.IGNORECASE,
)


def is_spurious_variable_placeholder_issue(
    issue: CriticIssueOut,
    segment: Segment | None,
    translation: str | None,
) -> bool:
    """Drop critic noise when only ``{{ ydb-short-name }}`` placement/count drifts."""
    if segment is None or translation is None or not issue.segment_id:
        return False
    if not _PLACEHOLDER_ISSUE.search(issue.category):
        return False
    return variable_placeholder_drift_only(segment.text, translation)


def is_spurious_cross_lang_placeholder_issue(
    issue: CriticIssueOut,
    segment: Segment | None,
    translation: str | None,
) -> bool:
    """Drop reorder/renumber noise when RU/EN share the same non-``⟦V⟧`` multiset."""
    if segment is None or translation is None or not issue.segment_id:
        return False
    if not _PLACEHOLDER_ISSUE.search(issue.category):
        return False
    if not cross_lang_placeholder_drift_only(segment.text, translation):
        return False
    haystack = f"{issue.category} {issue.comment}"
    return bool(_REORDER_ISSUE.search(haystack))


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
                "Ignoring spurious V-placeholder critic issue for %s",
                issue.segment_id,
            )
            continue
        if is_spurious_cross_lang_placeholder_issue(issue, seg, trans):
            logger.info(
                "Ignoring spurious cross-lang placeholder critic issue for %s",
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
