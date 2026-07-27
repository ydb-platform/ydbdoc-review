"""Named step lists for translate vs verify.

``doc_translate`` uses ``TRANSLATE_PROFILE`` (parse + translate only).
``doc_verify`` uses ``VERIFY_PROFILE`` (load EN + critic/heuristics/verdict).
``TRANSLATE_WITH_QA_PROFILE`` is for local ``translate-file --with-critic`` only.
"""

from __future__ import annotations

from dataclasses import dataclass

from ydbdoc_review.harness.steps import (
    CriticFeedbackRetryStep,
    CriticLoopStep,
    FinalizeEnStep,
    HeuristicsStep,
    HarnessStep,
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
    CriticLoopStep(),
    # Always translate residual Cyrillic in EN fences/prose (§6.136), even when
    # critic applied 0 segment fixes (Fixed segments: 0).
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
