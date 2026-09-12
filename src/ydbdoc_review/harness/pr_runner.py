"""Execute a PR-level harness profile over many file pairs."""

from __future__ import annotations

import hashlib

from ydbdoc_review.harness.pr_context import PRHarnessContext
from ydbdoc_review.harness.pr_profiles import PRHarnessProfile
from ydbdoc_review.harness.pr_state import PRRunState
from ydbdoc_review.pipeline.types import FinalTreeBlocker, PRTranslationResult


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
        soft_keep_blockers: list[FinalTreeBlocker] = []
        for run in state.pair_results:
            if run.soft_keep_reason is None or not run.target_text:
                continue
            reason = " ".join(run.soft_keep_reason.split())
            soft_keep_blockers.append(
                FinalTreeBlocker(
                    path=run.plan.target_path,
                    code="translation_soft_keep",
                    message=(
                        f"translation_soft_keep: {reason}. Действие: вручную обновить "
                        "EN в этой ветке, затем запустить doc_verify"
                    ),
                    artifact_sha256=hashlib.sha256(run.target_text.encode()).hexdigest(),
                )
            )
        return PRTranslationResult(
            pair_results=state.pair_results,
            final_tree_blockers=soft_keep_blockers,
        )
