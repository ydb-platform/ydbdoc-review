"""Named step lists for translate vs verify.

``doc_translate`` uses ``TRANSLATE_WITH_QA_PROFILE`` (translate + inline QA).
``doc_verify`` uses ``VERIFY_PROFILE`` (load EN + critic/heuristics/verdict).
``TRANSLATE_PROFILE`` remains the single-file translate-only profile.
"""

from __future__ import annotations

from dataclasses import dataclass

from ydbdoc_review.harness.steps import (
    CriticFeedbackRetryStep,
    CriticLoopStep,
    FinalizeEnStep,
    HarnessStep,
    HeuristicsStep,
    LoadTargetStep,
    ParseStep,
    ReportArtifactsStep,
    RoundTripStep,
    TranslateStep,
    VerdictStep,
)

_QA_TAIL: tuple[HarnessStep, ...] = (
    RoundTripStep(),
    CriticLoopStep(),
    HeuristicsStep(),
    VerdictStep(),
    ReportArtifactsStep(),
)

_TRANSLATE_QA_TAIL: tuple[HarnessStep, ...] = (
    RoundTripStep(),
    CriticLoopStep(),
    CriticFeedbackRetryStep(),
    HeuristicsStep(),
    VerdictStep(),
    ReportArtifactsStep(),
)

_VERIFY_QA_TAIL: tuple[HarnessStep, ...] = (
    RoundTripStep(),
    # Critic must inspect finalized bytes, not the mutable pre-finalize text.
    FinalizeEnStep(),
    CriticLoopStep(),
    # Re-apply deterministic protections after any critic suggestions.
    # Recommendation must match what we would commit / what already sits on
    # main after auto-fix — not the dirty incoming tip.
    FinalizeEnStep(),
    HeuristicsStep(),
    VerdictStep(),
    ReportArtifactsStep(),
)


@dataclass(frozen=True)
class HarnessProfile:
    """Ordered harness steps for one file run."""

    name: str
    steps: tuple[HarnessStep, ...]


TRANSLATE_PROFILE = HarnessProfile(
    name="translate",
    steps=(ParseStep(), TranslateStep()),
)

TRANSLATE_WITH_QA_PROFILE = HarnessProfile(
    name="translate",
    steps=(ParseStep(), TranslateStep(), *_TRANSLATE_QA_TAIL),
)

VERIFY_PROFILE = HarnessProfile(
    name="verify",
    steps=(ParseStep(), LoadTargetStep(), *_VERIFY_QA_TAIL),
)
