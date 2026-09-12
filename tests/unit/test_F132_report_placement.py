from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.usage import LLMUsage, UsageTracker
from ydbdoc_review.pipeline.types import FinalTreeBlocker, PRTranslationResult, PublicationImpact
from ydbdoc_review.reporting.builder import ReportMeta, build_full_report, build_source_pr_comment


def _config():
    return load_config(env={"YDBDOC_YC_FOLDER_ID": "f132", "YDBDOC_YC_API_KEY": "test"})


def test_F132_comment_order() -> None:
    result = PRTranslationResult(
        final_tree_blockers=[FinalTreeBlocker("en/a.md", "en_link_target", "missing")],
        publication_impact=PublicationImpact.PUBLISH_RED,
        yellow_warnings=["coverage fallback"],
    )
    usage = UsageTracker([LLMUsage("yandexgpt-5.1", 10, 2, 1.0, 0, True, role="analyze")])
    full = build_full_report(
        result,
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=_config(),
        usage=usage,
    )
    source = build_source_pr_comment(
        result,
        translation_pr_number=132,
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=_config(),
        usage=usage,
    )

    assert "QA K: RED" in full
    assert "Жёлтые предупреждения" in full
    assert "Translation PR | #132" in source
    assert "Стоимость" in source
    assert full != source


def test_F132_current_issues() -> None:
    result = PRTranslationResult(yellow_warnings=["coverage fallback"])
    report = build_full_report(
        result,
        meta=ReportMeta(mode="doc_verify", report_number=1, elapsed_s=1),
        config=_config(),
    )

    assert "Жёлтые предупреждения" in report
    assert "coverage fallback" in report
    assert "prompt" not in report.lower()
    assert "false suggestion" not in report.lower()
