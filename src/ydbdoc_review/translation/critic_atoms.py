"""Align actual target atom evidence and enforce its language independently of the LLM."""

from __future__ import annotations

from collections.abc import Mapping

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.placeholder_align import (
    normalize_target_segments_to_source,
    segment_atom_legend,
)
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.schemas import CriticIssueOut


def target_atom_maps(
    segments: list[Segment],
    translated_text: str,
) -> dict[str, dict[str, str]]:
    """Return target payloads under source segment IDs, or reject unsafe alignment."""
    targets = extract_segments(parse_markdown(translated_text))
    if len(segments) != len(targets):
        raise ValueError("Target atom alignment: segment count differs")
    if any(src.kind != tgt.kind for src, tgt in zip(segments, targets, strict=True)):
        raise ValueError("Target atom alignment: segment kind differs")
    aligned = normalize_target_segments_to_source(segments, targets)
    maps = {}
    for src, tgt in zip(segments, aligned, strict=True):
        legend = segment_atom_legend(tgt)
        if len(src.placeholders) != len(tgt.placeholders) or legend.keys() != (
            segment_atom_legend(src).keys()
        ):
            raise ValueError(f"Target atom alignment: protected markers differ for {src.id}")
        maps[src.id] = legend
    return maps


def protected_atom_language_issues(
    segments: list[Segment],
    *,
    target_atoms: Mapping[str, Mapping[str, str]] | None = None,
    target_lang: str = "en",
) -> list[CriticIssueOut]:
    """Inspect effective target code atoms; never replace missing target evidence."""
    from ydbdoc_review.validation.final_language import check_final_en_language

    issues = []
    for segment in segments:
        source_atoms = segment_atom_legend(segment)
        atoms = source_atoms if target_atoms is None else target_atoms.get(segment.id)
        if atoms is None or atoms.keys() != source_atoms.keys():
            issues.append(
                CriticIssueOut(
                    segment_id=segment.id,
                    severity="blocked",
                    category="protected_atom_alignment",
                    comment="Target atom evidence is missing or cannot be aligned safely",
                    suggested_text=None,
                )
            )
            # A partial map can still contain concrete language evidence.
            atoms = atoms or {}
        for marker, payload in atoms.items():
            if payload.startswith("code:") and check_final_en_language(
                payload[5:],
                target_lang=target_lang,
            ):
                issues.append(
                    CriticIssueOut(
                        segment_id=segment.id,
                        severity="blocked",
                        category="protected_atom_language",
                        comment=f"Untranslated human-language content in protected code atom {marker}",
                        suggested_text=None,
                    )
                )
    return issues
