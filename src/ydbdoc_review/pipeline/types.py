"""Pipeline result types."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from ydbdoc_review.llm.usage import UsageTracker
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.translation.manual import ManualAction
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse
from ydbdoc_review.validation.link_contract import LinkContractIssue

FileVerdict = Literal["ok", "warnings", "blocked"]


class PublicationImpact(StrEnum):
    """Whether a completed candidate may be published and how it must be signalled."""

    WITHHOLD_INCOMPLETE = "WITHHOLD_INCOMPLETE"
    WITHHOLD_UNSAFE = "WITHHOLD_UNSAFE"
    PUBLISH_RED = "PUBLISH_RED"
    PUBLISH_NORMAL = "PUBLISH_NORMAL"


@dataclass(frozen=True)
class FinalTreeBlocker:
    """PR-level deterministic blocker found against the assembled final tree."""

    path: str
    code: Literal["en_link_target", "translation_soft_keep"]
    message: str
    artifact_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.code == "translation_soft_keep":
            if not self.artifact_sha256 or not re.fullmatch(
                r"[0-9a-f]{64}", self.artifact_sha256
            ):
                raise ValueError("translation_soft_keep requires a lowercase SHA-256")
        elif self.artifact_sha256 is not None:
            raise ValueError("en_link_target must not carry an artifact SHA-256")


__all__ = [
    "FileTranslationResult",
    "FileVerdict",
    "FinalTreeBlocker",
    "ManualAction",
    "NavigationRunResult",
    "PRTranslationResult",
    "PairRunResult",
    "PublicationImpact",
]


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
    heuristic_blocking: list[str] = field(default_factory=list)
    heuristic_warnings: list[str] = field(default_factory=list)
    heuristic_info: list[str] = field(default_factory=list)
    manual_actions: list[ManualAction] = field(default_factory=list)
    segment_locations: dict[str, str] = field(default_factory=dict)
    segment_lines: dict[str, tuple[int, int]] = field(default_factory=dict)
    segment_excerpts: dict[str, str] = field(default_factory=dict)
    segment_source_excerpts: dict[str, str] = field(default_factory=dict)
    segment_alignment_error: str | None = None
    differential_meta: dict[str, object] = field(default_factory=dict)
    models_used: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    link_contract_issues: tuple[LinkContractIssue, ...] = ()

    @classmethod
    def from_usage(
        cls,
        *,
        tracker: UsageTracker,
        record_start: int = 0,
        **kwargs: object,
    ) -> FileTranslationResult:
        metrics = tracker.metrics_since(record_start)
        data = dict(kwargs)
        data.setdefault("models_used", metrics["models_used"])
        data.setdefault("input_tokens", metrics["input_tokens"])
        data.setdefault("output_tokens", metrics["output_tokens"])
        data.setdefault("estimated_cost_usd", metrics["estimated_cost_usd"])
        return cls(**data)  # type: ignore[arg-type]


@dataclass
class NavigationRunResult:
    """Outcome of scoped navigation YAML merge for one RU/EN pair."""

    ru_path: str
    en_path: str
    kind: str
    target_text: str | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    verdict: FileVerdict = "ok"


@dataclass
class PairRunResult:
    """Outcome for one pair in a PR translation run."""

    plan: PairPlan
    target_text: str | None = None
    deleted: bool = False
    skipped: bool = False
    file_result: FileTranslationResult | None = None
    error: str | None = None
    # Typed provenance: translation failed and exact pre-existing target was retained.
    soft_keep_reason: str | None = None
    # RU/EN source body actually used for this run (verify pick / merge ref).
    source_text: str | None = None
    validation_issues: tuple[LinkContractIssue, ...] = ()


@dataclass
class PRTranslationResult:
    """Aggregate outcome for a PR-level translation job."""

    pair_results: list[PairRunResult] = field(default_factory=list)
    navigation_results: list[NavigationRunResult] = field(default_factory=list)
    completeness_gaps: list[str] = field(default_factory=list)
    final_tree_blockers: list[FinalTreeBlocker] = field(default_factory=list)
    publication_impact: PublicationImpact = PublicationImpact.PUBLISH_NORMAL
    # REQUIREMENTS §10/§12 tip-newer overwrite notices (yellow; never blockers).
    yellow_warnings: list[str] = field(default_factory=list)
    # Set only after a publication attempt proves there is no real git artifact.
    publication_failure: str | None = None

    @property
    def translated_count(self) -> int:
        md = sum(
            1
            for r in self.pair_results
            if r.file_result is not None
            and not r.skipped
            and not r.deleted
            and r.soft_keep_reason is None
        )
        nav = sum(1 for n in self.navigation_results if n.target_text is not None and not n.error)
        return md + nav

    @property
    def failed_count(self) -> int:
        return sum(
            1
            for r in self.pair_results
            if r.error is not None
            or (r.soft_keep_reason is not None and not (r.target_text or "").strip())
        )

    @property
    def retained_count(self) -> int:
        return sum(
            1
            for r in self.pair_results
            if r.soft_keep_reason is not None and bool((r.target_text or "").strip())
        )

    @property
    def has_soft_keep(self) -> bool:
        return any(r.soft_keep_reason is not None for r in self.pair_results)

    def usage_summary(self, tracker: UsageTracker) -> dict[str, float | int | list[str]]:
        return {
            "input_tokens": tracker.total_input_tokens,
            "output_tokens": tracker.total_output_tokens,
            "estimated_cost_usd": tracker.estimate_cost_usd(),
            "models_used": sorted({r.model_slug for r in tracker.records if r.success}),
        }
