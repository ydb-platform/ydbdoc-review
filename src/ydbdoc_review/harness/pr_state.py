"""PR-level harness state."""

from __future__ import annotations

from dataclasses import dataclass, field

from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.types import PairRunResult


@dataclass
class PRRunState:
    """Mutable state for one PR translation or verify run."""

    contents: list[PairContent]
    plans: list[PairPlan] = field(default_factory=list)
    pair_results: list[PairRunResult] = field(default_factory=list)
    cache: dict[str, str] = field(default_factory=dict)
