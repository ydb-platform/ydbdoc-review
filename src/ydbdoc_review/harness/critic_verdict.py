"""Critic verdict aggregation for harness QA step."""

from __future__ import annotations

from ydbdoc_review.pipeline.types import FileVerdict
from ydbdoc_review.translation.schemas import CriticResponse


def compute_critic_verdict(
    *,
    initial: CriticResponse | None,
    unresolved: CriticResponse | None,
) -> FileVerdict:
    if unresolved is None:
        if initial is None:
            return "ok"
        if not initial.issues:
            if initial.verdict == "blocked":
                return "blocked"
            return "ok"
        if initial.verdict == "blocked":
            return "blocked"
        return "warnings"
    if unresolved.verdict == "blocked":
        return "blocked"
    if unresolved.issues:
        if any(i.severity == "blocked" for i in unresolved.issues):
            return "blocked"
        return "warnings"
    return "ok"
