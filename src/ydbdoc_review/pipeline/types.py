"""Pipeline result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ydbdoc_review.llm.usage import UsageTracker
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.translation.manual import ManualAction
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse

FileVerdict = Literal["ok", "warnings", "blocked"]

__all__ = ["ManualAction", "FileTranslationResult", "FileVerdict", "PairRunResult", "PRTranslationResult"]

@dataclass
class FileTranslationResult:
    """Outcome of translating one markdown file."""

    file_path: str
    final_text: str
    segments_count: int
    verdict: FileVerdict
    prompt_version: str
    critic_initial: CriticResponse | None = None
    critic_applied: list[CriticIssueOut] = field(default_factory=list)
    critic_skipped: list[CriticIssueOut] = field(default_factory=list)
    critic_unresolved: CriticResponse | None = None
    heuristic_warnings: list[str] = field(default_factory=list)
    manual_actions: list[ManualAction] = field(default_factory=list)
    segment_locations: dict[str, str] = field(default_factory=dict)
    segment_lines: dict[str, tuple[int, int]] = field(default_factory=dict)
    models_used: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0

    @classmethod
    def from_usage(
        cls,
        *,
        tracker: UsageTracker,
        **kwargs: object,
    ) -> FileTranslationResult:
        models = sorted({r.model_slug for r in tracker.records if r.success})
        data = dict(kwargs)
        data.setdefault("models_used", models)
        data.setdefault("input_tokens", tracker.total_input_tokens)
        data.setdefault("output_tokens", tracker.total_output_tokens)
        data.setdefault("estimated_cost_usd", tracker.estimate_cost_usd())
        return cls(**data)  # type: ignore[arg-type]


@dataclass
class PairRunResult:
    """Outcome for one pair in a PR translation run."""

    plan: PairPlan
    target_text: str | None = None
    deleted: bool = False
    skipped: bool = False
    file_result: FileTranslationResult | None = None
    error: str | None = None


@dataclass
class PRTranslationResult:
    """Aggregate outcome for a PR-level translation job."""

    pair_results: list[PairRunResult] = field(default_factory=list)

    @property
    def translated_count(self) -> int:
        return sum(
            1
            for r in self.pair_results
            if r.file_result is not None and not r.skipped and not r.deleted
        )

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.pair_results if r.error is not None)

    def usage_summary(self, tracker: UsageTracker) -> dict[str, float | int | list[str]]:
        return {
            "input_tokens": tracker.total_input_tokens,
            "output_tokens": tracker.total_output_tokens,
            "estimated_cost_usd": tracker.estimate_cost_usd(),
            "models_used": sorted({r.model_slug for r in tracker.records if r.success}),
        }
