"""F-094: quality reports update without mutating the author's PR."""

from __future__ import annotations

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    FinalTreeBlocker,
    PairRunResult,
    PRTranslationResult,
)
from ydbdoc_review.reporting.builder import ReportMeta, build_full_report


def _report(result: PRTranslationResult) -> str:
    return build_full_report(
        result,
        meta=ReportMeta(mode="doc_verify", report_number=2, elapsed_s=1),
        config=load_config(
            env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}
        ),
    )


def _clean_result() -> PRTranslationResult:
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    return PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                file_result=FileTranslationResult(
                    file_path=pair.en_path,
                    final_text="Hello",
                    segments_count=1,
                    verdict="ok",
                    prompt_version="v1",
                ),
            )
        ]
    )


def test_F094_red_to_green() -> None:
    result = _clean_result()
    result.final_tree_blockers.append(
        FinalTreeBlocker(
            path="ydb/docs/en/a.md",
            code="en_link_target",
            message="missing target",
        )
    )

    red_report = _report(result)
    assert "Статус QA (K): 🔴 RED" in red_report

    result.final_tree_blockers.clear()
    green_report = _report(result)
    assert "Статус QA (K): 🟢 GREEN" in green_report
    assert "Статус QA (K): 🔴 RED" not in green_report
    assert "Рекомендация:" not in green_report


def test_F094_no_author_mutation() -> None:
    """The GitHub client exposes reporting/comment primitives, not author-PR mutations."""
    for forbidden in (
        "merge_pull_request",
        "approve_pull_request",
        "close_pull_request",
    ):
        assert not hasattr(GitHubClient, forbidden)
