"""F-039 contracts for unresolved Markdown dependencies."""

from __future__ import annotations

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    PairRunResult,
    PRTranslationResult,
)
from ydbdoc_review.reporting.builder import ReportMeta, build_full_report


def _cfg():
    return load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})


def _result(*, target_text: str = "See [dependency](missing.md).\n") -> PRTranslationResult:
    pair = DocPair(
        ru_path="ydb/docs/ru/core/page.md",
        en_path="ydb/docs/en/core/page.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="RU changed, EN missing",
    )
    return PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                target_text=target_text,
                file_result=FileTranslationResult(
                    file_path=pair.en_path,
                    final_text=target_text,
                    segments_count=1,
                    verdict="ok",
                    prompt_version="v1",
                ),
            )
        ],
        yellow_warnings=[
            "link dependency budget exhausted (20): missing EN for "
            "ydb/docs/en/core/limit.md; manual action required — "
            "translate or allow doc_continue",
            "ydb/docs/en/core/excluded.md: dependency excluded by policy; "
            "manual action required — translate or allow doc_continue",
            "ydb/docs/en/core/missing-source.md: RU source is unavailable; "
            "manual action required — translate or allow doc_continue",
        ],
    )


def test_F039_three_causes() -> None:
    body = build_full_report(
        _result(),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=_cfg(),
    )

    assert "QA RED" in body
    for path in (
        "ydb/docs/en/core/limit.md",
        "ydb/docs/en/core/excluded.md",
        "ydb/docs/en/core/missing-source.md",
    ):
        assert path in body
    assert body.count("translate or allow doc_continue") == 3


def test_F039_keep_result() -> None:
    result = _result()
    body = build_full_report(
        result,
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=1),
        config=_cfg(),
    )

    assert result.pair_results[0].target_text == "See [dependency](missing.md).\n"
    assert "See [dependency](missing.md)." not in body
    assert "ydb/docs/en/core/missing-source.md" in body
    assert "🔴" in body
