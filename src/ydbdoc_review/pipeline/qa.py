"""Unified file-level QA: round-trip align, heuristics, verdict (translate + verify)."""

from __future__ import annotations

from typing import Literal

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.placeholder_align import (
    normalize_target_segments_to_source,
)
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.validation.heuristics import (
    ClassifiedHeuristics,
    bump_verdict_for_blocking_heuristics,
)

FileVerdict = Literal["ok", "warnings", "blocked"]


def align_translations_from_target(
    source_segments: list[Segment],
    target_text: str,
) -> dict[str, str]:
    """Map source segment ids → texts from a rendered EN file (round-trip gate).

    Target segments are renumbered so each shared inline atom uses the source
    placeholder name. The critic and apply path then see consistent ``⟦Xn⟧``
    semantics across RU/EN — same name = same atom — instead of independent
    left-to-right numbering, which causes spurious "placeholder order
    mismatch" reports when word order shifts in translation.
    """
    target_segments_raw = extract_segments(parse_markdown(target_text))
    if len(target_segments_raw) != len(source_segments):
        raise TranslationValidationError(
            f"segment count mismatch: source {len(source_segments)} vs "
            f"target {len(target_segments_raw)}"
        )
    target_segments = normalize_target_segments_to_source(
        source_segments, target_segments_raw
    )
    return {
        src.id: tgt.text
        for src, tgt in zip(source_segments, target_segments, strict=True)
    }


def gate_round_trip(
    segments: list[Segment],
    target_text: str,
) -> tuple[dict[str, str], str | None]:
    """Return (translations, alignment_error). Error text is set when gate fails."""
    try:
        return align_translations_from_target(segments, target_text), None
    except TranslationValidationError as exc:
        return {}, str(exc)


def compose_file_verdict(
    *,
    critic_verdict: FileVerdict,
    alignment_error: str | None,
    heuristics: ClassifiedHeuristics,
    manual_actions: bool,
) -> FileVerdict:
    """Single verdict rule for doc_translate and doc_verify."""
    if alignment_error:
        return "blocked"
    verdict = critic_verdict
    verdict = bump_verdict_for_blocking_heuristics(verdict, heuristics.blocking)
    if heuristics.warnings and verdict == "ok":
        verdict = "warnings"
    if manual_actions and verdict == "ok":
        verdict = "warnings"
    return verdict
