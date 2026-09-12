"""F-096: publication status and QA quality stay separate."""

from __future__ import annotations

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.types import (
    FinalTreeBlocker,
    NavigationRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    build_full_report,
    build_translation_pr_body,
)


def _config():
    return load_config(env={"YDBDOC_YC_FOLDER_ID": "f096", "YDBDOC_YC_API_KEY": "test"})


def _red_result() -> PRTranslationResult:
    return PRTranslationResult(
        final_tree_blockers=[
            FinalTreeBlocker(
                path="ydb/docs/en/a.md",
                code="en_link_target",
                message="missing target",
            )
        ],
        publication_impact=PublicationImpact.PUBLISH_RED,
    )


def test_F096_published_red() -> None:
    body = build_translation_pr_body(
        96,
        "o/r",
        publication_result=_red_result(),
    )

    assert "Артефакт: опубликован" in body
    assert "QA K: 🔴 RED" in body
    assert "не мержить" not in body
    assert "можно мержить" not in body


def test_F096_ci_excluded() -> None:
    result = PRTranslationResult(
        navigation_results=[
            NavigationRunResult(
                ru_path="ydb/docs/ru/toc.yaml",
                en_path="ydb/docs/en/toc.yaml",
                kind="toc",
                target_text="items: []\n",
            )
        ],
        publication_impact=PublicationImpact.PUBLISH_NORMAL,
    )
    report = build_full_report(
        result,
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=_config(),
    )

    assert "Статус QA (K): 🟢 GREEN" in report
    assert "CI" not in report
    assert "merge-ready" not in report
    assert "можно мержить" not in report
