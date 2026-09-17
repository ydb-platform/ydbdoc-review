"""F-074: an unknown successor keeps the old route until an operator answers."""

from __future__ import annotations

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.navigation.redirects import (
    follow_redirect_repo_md_path,
    should_skip_redirect_tombstone_en,
)
from ydbdoc_review.ops.continue_cmd import parse_continue_instruction
from ydbdoc_review.pipeline.types import NavigationRunResult, PRTranslationResult
from ydbdoc_review.reporting.builder import ReportMeta, build_full_report
from ydbdoc_review.reporting.heuristic_messages import format_heuristic_reviewer_detail

OLD_EN = "ydb/docs/en/core/guide/legacy.md"
REDIRECT = "common:\n  - from: /guide/legacy.md\n    to: /guide/current.md\n"


def test_F074_keep_old() -> None:
    """Without a successor, the live file and public route remain unchanged."""
    assert follow_redirect_repo_md_path(OLD_EN, "") == OLD_EN
    assert not should_skip_redirect_tombstone_en(
        OLD_EN,
        redirect_source_en_paths=set(),
    )

    detail = format_heuristic_reviewer_detail(
        f"orphan_toc_page: translated EN page `{OLD_EN}` has no unambiguous successor"
    )
    assert detail.suggestion is not None
    assert "какой существующий адрес той же локали" in detail.suggestion.lower()
    assert "/ydbdoc continue <адрес>" in detail.suggestion

    report = build_full_report(
        PRTranslationResult(
            navigation_results=[
                NavigationRunResult(
                    ru_path="ydb/docs/ru/core/guide/toc.yaml",
                    en_path="ydb/docs/en/core/guide/toc.yaml",
                    kind="toc",
                    target_text="items:\n",
                    warnings=[
                        f"orphan_toc_page: translated EN page `{OLD_EN}` "
                        "has no unambiguous successor"
                    ],
                    verdict="blocked",
                )
            ]
        ),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}),
    )
    assert "🔴" in report
    assert "Какой существующий адрес той же локали" in report


def test_F074_operator_answer() -> None:
    """An operator address is accepted, but deletion is safe only with redirect."""
    answer = parse_continue_instruction(
        "/ydbdoc continue /guide/current.md"
    )
    assert answer == "/guide/current.md"

    assert follow_redirect_repo_md_path(OLD_EN, REDIRECT) == (
        "ydb/docs/en/core/guide/current.md"
    )
    assert should_skip_redirect_tombstone_en(
        OLD_EN,
        redirect_source_en_paths={OLD_EN},
    )
    assert not should_skip_redirect_tombstone_en(
        OLD_EN,
        redirect_source_en_paths=set(),
    )
