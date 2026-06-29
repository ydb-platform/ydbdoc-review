"""Execute a harness profile over one file."""

from __future__ import annotations

from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.profiles import HarnessProfile
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.pipeline.types import FileTranslationResult


class FileHarness:
    """Run an ordered list of steps; mutates ``FileRunState`` in place."""

    def __init__(self, profile: HarnessProfile) -> None:
        self._profile = profile

    @property
    def profile_name(self) -> str:
        return self._profile.name

    def run(self, state: FileRunState, ctx: HarnessContext) -> FileTranslationResult:
        for step in self._profile.steps:
            if state.stopped_early:
                break
            step.run(state, ctx)
        return self._to_result(state, ctx)

    def _to_result(
        self, state: FileRunState, ctx: HarnessContext
    ) -> FileTranslationResult:
        heuristics = state.heuristics
        return FileTranslationResult.from_usage(
            tracker=ctx.client.usage_tracker,
            record_start=ctx.usage_record_start,
            file_path=state.file_path,
            final_text=state.translated_text,
            segments_count=len(state.segments),
            verdict=state.verdict if not state.stopped_early else "ok",
            prompt_version=ctx.prompt_version,
            critic_initial=state.critic_initial,
            critic_applied=state.critic_applied,
            critic_skipped=state.critic_skipped,
            critic_unresolved=state.critic_unresolved,
            heuristic_blocking=heuristics.blocking if heuristics else [],
            heuristic_warnings=heuristics.warnings if heuristics else [],
            heuristic_info=heuristics.info if heuristics else [],
            manual_actions=state.manual_actions,
            segment_locations=state.segment_locations,
            segment_lines=state.segment_lines,
            segment_excerpts=state.segment_excerpts,
            segment_alignment_error=state.segment_alignment_error,
        )
