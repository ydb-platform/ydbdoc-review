"""PR-level harness profiles."""

from __future__ import annotations

from dataclasses import dataclass

from ydbdoc_review.harness.pr_steps import (
    ExecutePairPlansStep,
    PlanTranslatePairsStep,
    PlanVerifyPairsStep,
    PRHarnessStep,
)


@dataclass(frozen=True)
class PRHarnessProfile:
    name: str
    steps: tuple[PRHarnessStep, ...]


TRANSLATE_PR_PROFILE = PRHarnessProfile(
    name="translate_pr",
    steps=(PlanTranslatePairsStep(), ExecutePairPlansStep()),
)

VERIFY_PR_PROFILE = PRHarnessProfile(
    name="verify_pr",
    steps=(PlanVerifyPairsStep(), ExecutePairPlansStep()),
)
