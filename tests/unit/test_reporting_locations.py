"""Tests for report location helpers."""

from __future__ import annotations

from ydbdoc_review.pipeline.types import ManualAction
from ydbdoc_review.reporting.locations import (
    ReportLinkContext,
    build_segment_line_map,
    consolidate_heuristic_warnings,
    filter_critic_for_report,
    format_location_label,
    manual_action_segment_ids,
)
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.schemas import CriticIssueOut


def test_filter_critic_drops_missing_translation_when_manual():
    manual = {ManualAction("s0124", "table:row1:col2", "manual")}
    issues = [
        CriticIssueOut(
            segment_id="s0124",
            severity="warning",
            category="missing translation",
            comment="Segment not translated",
            suggested_text="EN text",
        ),
        CriticIssueOut(
            segment_id="s0042",
            severity="warning",
            category="terminology",
            comment="term",
            suggested_text=None,
        ),
    ]
    filtered = filter_critic_for_report(issues, manual_action_segment_ids(manual))
    assert len(filtered) == 1
    assert filtered[0].segment_id == "s0042"


def test_consolidate_cyrillic_with_manual_action():
    warnings = [
        "Кириллица в EN-тексте (строка ~355): «режим»",
        "Кириллица в EN-тексте (строка ~355): «пакет»",
        "… и ещё 549 вхождений кириллицы (всего 561 символов)",
    ]
    out = consolidate_heuristic_warnings(
        warnings,
        manual_ids={"s0124"},
        manual_line_ranges=[(355, 358)],
    )
    assert len(out) == 1
    assert "сегмент `s0124`" in out[0]
    assert "561" in out[0]


def test_format_location_label_with_github_link():
    label = format_location_label(
        file_path="ydb/docs/en/a.md",
        segment_id="s1",
        path_label="table:row1:col2",
        line_range=(355, 355),
        link=ReportLinkContext(github_repo="org/repo", ref="main"),
    )
    assert "table:row1:col2" in label
    assert "github.com/org/repo/blob/main" in label
    assert "355" in label


def test_build_segment_line_map():
    seg = Segment(
        id="s1",
        kind=SegmentKind.PARAGRAPH,
        path=["Intro"],
        text="Используйте `--input-batch` для пакетов.",
        placeholders=[],
        ast_path=[0],
    )
    final = "# Title\n\nUse `--input-batch` for batches.\n"
    lines = build_segment_line_map(
        final, [seg], {"s1": "Use `--input-batch` for batches."}
    )
    assert lines["s1"] == (3, 3)
