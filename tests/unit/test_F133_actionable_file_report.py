from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    _format_reviewer_item,
    build_source_pr_comment,
)


def _config():
    return load_config(env={"YDBDOC_YC_FOLDER_ID": "f133", "YDBDOC_YC_API_KEY": "test"})


def test_F133_actionable_issue() -> None:
    item = _format_reviewer_item(
        index=1,
        location="docs/en/a.md, section Intro, line 12",
        problem="Source heading is not represented in the target. Add the section to EN.",
        severity="blocked",
        source_excerpt="## Настройки",
        target_excerpt="## Settings",
        source_lang="ru",
        target_lang="en",
    )

    assert "docs/en/a.md" in item
    assert "Intro" in item
    assert "Настройки" in item and "Settings" in item
    assert "Add the section" in item
    assert "Exception" not in item


def test_F133_file_categories() -> None:
    result = PRTranslationResult()
    report = build_source_pr_comment(
        result,
        translation_pr_number=None,
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=_config(),
    )

    assert "перевод не требуется" in report or "Translation PR не создаётся" in report
    assert "нет коммита" in report
    assert "новый перевод" not in report.lower()
