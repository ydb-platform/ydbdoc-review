"""F-084: unknown TOC placement is not guessed and remains RED."""

from __future__ import annotations

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.navigation.toc import merge_en_toc_yaml
from ydbdoc_review.pipeline.types import NavigationRunResult, PRTranslationResult
from ydbdoc_review.reporting.builder import ReportMeta, build_full_report
from ydbdoc_review.reporting.heuristic_messages import format_heuristic_reviewer_detail


def test_F084_unknown_position() -> None:
    source_toc = "items:\n- name: Existing\n  href: existing.md\n"
    ru_pr = source_toc + "- name: New\n  href: unknown/new.md\n"
    merged = merge_en_toc_yaml(
        source_toc,
        ru_pr,
        translate_hrefs=set(),
        translate_name=lambda name: name,
        restrict_gap_fill_to_scope=True,
    )
    assert "unknown/new.md" not in merged
    assert source_toc.splitlines() == [
        "items:",
        "- name: Existing",
        "  href: existing.md",
    ]

    detail = format_heuristic_reviewer_detail(
        "orphan_toc_page: translated EN page `ydb/docs/en/core/unknown/new.md` "
        "has no unambiguous TOC position"
    )
    assert detail.suggestion is not None
    assert "целевого TOC" in detail.suggestion


def test_F084_source_orphan() -> None:
    warning = (
        "orphan_toc_page: translated EN page `ydb/docs/en/core/unknown/new.md` "
        "has no unambiguous TOC position"
    )
    report = build_full_report(
        PRTranslationResult(
            navigation_results=[
                NavigationRunResult(
                    ru_path="ydb/docs/ru/core/toc_p.yaml",
                    en_path="ydb/docs/en/core/toc_p.yaml",
                    kind="toc",
                    target_text="items:\n",
                    warnings=[warning],
                    verdict="blocked",
                )
            ]
        ),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}),
    )
    assert "🔴" in report
    assert "целевого TOC" in report
    assert "старый маршрут удалять нельзя" in report
