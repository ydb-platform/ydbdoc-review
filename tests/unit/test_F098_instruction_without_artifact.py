"""F-098: unresolved instructions never become a green no-op artifact."""

from __future__ import annotations

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.types import NavigationRunResult, PRTranslationResult
from ydbdoc_review.reporting.builder import ReportMeta, build_source_pr_comment


def _cfg():
    return load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})


def _unresolved_result() -> PRTranslationResult:
    return PRTranslationResult(
        navigation_results=[
            NavigationRunResult(
                ru_path="ydb/docs/ru/core/guide/toc.yaml",
                en_path="ydb/docs/en/core/guide/toc.yaml",
                kind="toc",
                warnings=[
                    "orphan_toc_page: old EN route has no unambiguous successor"
                ],
                verdict="blocked",
            )
        ],
        publication_failure="awaiting_instruction_no_artifact",
    )


def test_F098_delete_only() -> None:
    body = build_source_pr_comment(
        _unresolved_result(),
        translation_pr_number=None,
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=_cfg(),
        committed=False,
    )

    assert "awaiting_instruction_no_artifact" in body
    assert "какой существующий адрес" in body
    assert "/ydbdoc continue" in body
    assert "translation PR **не создан**" in body
    assert "перевод не требуется" not in body


def test_F098_continue_existing() -> None:
    body = build_source_pr_comment(
        _unresolved_result(),
        translation_pr_number=42,
        meta=ReportMeta(mode="doc_continue", report_number=1, elapsed_s=1),
        config=_cfg(),
        committed=False,
    )

    assert "Translation PR | #42" in body
    assert "QA RED" in body
    assert "пуст" not in body.lower()
