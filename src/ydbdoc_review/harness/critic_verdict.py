"""Critic verdict aggregation for harness QA step."""

from __future__ import annotations

from ydbdoc_review.pipeline.types import FileVerdict
from ydbdoc_review.translation.schemas import CriticResponse


def compute_critic_verdict(
    *,
    initial: CriticResponse | None,
    unresolved: CriticResponse | None,
) -> FileVerdict:
    response = unresolved if unresolved is not None else initial
    if response is None:
        return "ok"
    if response.verdict == "blocked" or any(i.severity == "blocked" for i in response.issues):
        return "blocked"
    if response.issues or response.verdict == "warnings":
        return "warnings"
    return "ok"
