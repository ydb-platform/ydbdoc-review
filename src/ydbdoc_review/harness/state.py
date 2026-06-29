"""Mutable per-file state passed through harness steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ydbdoc_review.parsing.ast_types import Document
from ydbdoc_review.pipeline.types import FileVerdict
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.manual import ManualAction
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse
from ydbdoc_review.validation.heuristics import ClassifiedHeuristics

HarnessMode = Literal["translate", "verify"]


@dataclass
class FileRunState:
    """Artifacts accumulated while processing one markdown file."""

    mode: HarnessMode
    file_path: str
    raw_source_text: str
    source_text: str
    existing_target_text: str | None = None

    source_doc: Document | None = None
    segments: list[Segment] = field(default_factory=list)
    segment_locations: dict[str, str] = field(default_factory=dict)

    translations: dict[str, str] = field(default_factory=dict)
    translated_text: str = ""

    render_base_doc: Document | None = None
    render_base_segments: list[Segment] = field(default_factory=list)
    fence_reference_text: str = ""

    manual_actions: list[ManualAction] = field(default_factory=list)
    segment_alignment_error: str | None = None

    critic_initial: CriticResponse | None = None
    critic_applied: list[CriticIssueOut] = field(default_factory=list)
    critic_skipped: list[CriticIssueOut] = field(default_factory=list)
    critic_unresolved: CriticResponse | None = None
    critic_verdict: FileVerdict = "ok"

    heuristics: ClassifiedHeuristics | None = None
    verdict: FileVerdict = "ok"

    segment_lines: dict[str, tuple[int, int]] = field(default_factory=dict)
    segment_excerpts: dict[str, str] = field(default_factory=dict)

    stopped_early: bool = False
    translate_retry_count: int = 0
