"""F-093: RED translation/fixup PRs stay open and actionable."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import Mock

from tests.unit.test_reporting_builder import _sample_result
from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github import workflow
from ydbdoc_review.pipeline.types import FinalTreeBlocker
from ydbdoc_review.reporting.builder import ReportMeta, build_source_pr_comment


def test_F093_pr_types() -> None:
    """Translation and fixup publication paths always create open PRs."""
    translation_source = inspect.getsource(workflow.run_doc_translate)
    verify_source = inspect.getsource(workflow.run_doc_verify)

    assert "draft=publish_red" not in translation_source
    assert "draft=True" not in translation_source
    assert "draft=False" in translation_source
    assert "draft=False" in verify_source


def test_F093_red_summary() -> None:
    """RED summary lists current defects, action, and preserves author PR mode."""
    result = _sample_result(new_file=True)
    result.final_tree_blockers = [
        FinalTreeBlocker(
            path="ydb/docs/en/a.md",
            code="en_link_target",
            message="missing target page",
        )
    ]
    result.publication_impact = "PUBLISH_RED"
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})

    summary = build_source_pr_comment(
        result,
        translation_pr_number=99,
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=cfg,
    )

    assert "QA RED, do not merge" in summary
    assert "Кириллица в EN-тексте" in summary
    assert "Таблица не переведена автоматически" in summary
    assert "missing target page" in summary
    assert "Следующее действие" in summary
    gh = SimpleNamespace(convert_pull_to_draft=Mock())
    workflow._convert_translation_pr_to_draft_if_allowed(
        gh,
        "owner",
        "repo",
        7,
        translation_pr=True,
        verify_requires_red=True,
        dry_run=False,
        no_commit=False,
    )
    gh.convert_pull_to_draft.assert_not_called()
