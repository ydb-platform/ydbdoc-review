"""Named step lists for translate vs verify.

``doc_translate`` uses ``TRANSLATE_WITH_QA_PROFILE`` (translate + inline QA).
``doc_verify`` uses ``VERIFY_PROFILE`` (load EN + critic/heuristics/verdict).
``TRANSLATE_PROFILE`` is the single-file profile without a critic.
Every profile finishes with deterministic finalization, heuristics, verdict, and
report artifacts. Only the translate QA and verify profiles call a critic.
"""

from __future__ import annotations

from dataclasses import dataclass

from ydbdoc_review.harness.steps import (
    CriticFeedbackRetryStep,
    CriticLoopStep,
    FinalizeEnStep,
    FinalLanguageStep,
    HarnessStep,
    HeuristicsStep,
    LoadTargetStep,
    ParseStep,
    ReportArtifactsStep,
    RoundTripStep,
    TranslateStep,
    VerdictStep,
)

_TRANSLATE_QA_TAIL: tuple[HarnessStep, ...] = (
    RoundTripStep(),
    CriticLoopStep(),
    CriticFeedbackRetryStep(),
    FinalizeEnStep(),
    HeuristicsStep(),
    FinalLanguageStep(),
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
    FinalLanguageStep(),
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
    steps=(
        ParseStep(),
        TranslateStep(),
        FinalizeEnStep(),
        HeuristicsStep(),
        FinalLanguageStep(),
        VerdictStep(),
        ReportArtifactsStep(),
    ),
)

TRANSLATE_WITH_QA_PROFILE = HarnessProfile(
    name="translate",
    steps=(ParseStep(), TranslateStep(), *_TRANSLATE_QA_TAIL),
)

VERIFY_PROFILE = HarnessProfile(
    name="verify",
    steps=(ParseStep(), LoadTargetStep(), *_VERIFY_QA_TAIL),
)

# Existing targets in a doc_translate scope still finish mechanical preparation
# before the PR-level immutable review. Standalone VERIFY_PROFILE is unchanged.
PREPARE_EXISTING_PROFILE = HarnessProfile(
    name="verify",
    steps=(ParseStep(), LoadTargetStep(), FinalizeEnStep(), HeuristicsStep(),
           FinalLanguageStep(), VerdictStep(), ReportArtifactsStep()),
)
