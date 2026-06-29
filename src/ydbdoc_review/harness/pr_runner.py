"""Execute a PR-level harness profile over many file pairs."""

from __future__ import annotations

from ydbdoc_review.harness.pr_context import PRHarnessContext
from ydbdoc_review.harness.pr_profiles import PRHarnessProfile
from ydbdoc_review.harness.pr_state import PRRunState
from ydbdoc_review.pipeline.types import PRTranslationResult


class PRHarness:
    """Plan and run all pair files for doc_translate or doc_verify."""

    def __init__(self, profile: PRHarnessProfile) -> None:
        self._profile = profile

    @property
    def profile_name(self) -> str:
        return self._profile.name

    def run(self, state: PRRunState, ctx: PRHarnessContext) -> PRTranslationResult:
        for step in self._profile.steps:
            step.run(state, ctx)
        return PRTranslationResult(pair_results=state.pair_results)
