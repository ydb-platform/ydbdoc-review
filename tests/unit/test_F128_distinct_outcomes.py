import pytest

from ydbdoc_review.pipeline.types import (
    FinalTreeBlocker,
    NavigationRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.reporting.builder import (
    _artifact_status,
    _qa_status,
    build_translation_pr_body,
)


@pytest.mark.parametrize(
    ("result", "quality"),
    [
        (PRTranslationResult(), "⚪ нет обработанных файлов"),
        (
            PRTranslationResult(
                navigation_results=[
                    NavigationRunResult("ru/toc.yaml", "en/toc.yaml", "toc")
                ]
            ),
            "🟢 GREEN",
        ),
        (
            PRTranslationResult(
                navigation_results=[
                        NavigationRunResult(
                        "ru/toc.yaml",
                        "en/toc.yaml",
                        "toc",
                        warnings=["warning"],
                        verdict="warnings",
                    )
                ]
            ),
            "🟡 YELLOW",
        ),
        (
            PRTranslationResult(
                final_tree_blockers=[
                    FinalTreeBlocker("en/a.md", "en_link_target", "missing target")
                ],
                publication_impact=PublicationImpact.WITHHOLD_UNSAFE,
            ),
            "🔴 RED",
        ),
    ],
)
def test_F128_state_table(result: PRTranslationResult, quality: str) -> None:
    _, label = _qa_status(result)
    assert quality in f"{_qa_status(result)[0]} {_qa_status(result)[1]}"
    assert _artifact_status(result) == "не опубликован" or result.publication_impact in {
        PublicationImpact.PUBLISH_NORMAL,
        PublicationImpact.PUBLISH_RED,
    }
    assert label


def test_F128_exit_zero() -> None:
    result = PRTranslationResult(
        final_tree_blockers=[
            FinalTreeBlocker("en/a.md", "en_link_target", "missing target")
        ],
        publication_impact=PublicationImpact.WITHHOLD_UNSAFE,
    )
    body = build_translation_pr_body(128, "ydb-platform/ydb", publication_result=result)

    assert "QA K: 🔴 RED" in body
    assert "Артефакт: не опубликован" in body
    assert "QA K: 🟢 GREEN" not in body
