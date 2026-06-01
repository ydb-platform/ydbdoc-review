"""Tests for markdown report builder."""

from __future__ import annotations

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import FileTranslationResult, PRTranslationResult, PairRunResult
from ydbdoc_review.reporting.builder import (
    ReportMeta,
    build_commit_message,
    build_full_report,
    build_source_pr_comment,
)


def _sample_result() -> PRTranslationResult:
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    fr = FileTranslationResult(
        file_path=pair.en_path,
        final_text="Hello",
        segments_count=1,
        verdict="ok",
        prompt_version="v1",
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd=0.01,
        models_used=["yandexgpt"],
    )
    return PRTranslationResult(
        pair_results=[
            PairRunResult(plan=plan, target_text="Hello", file_result=fr),
        ]
    )


def test_build_source_pr_comment():
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    body = build_source_pr_comment(
        _sample_result(),
        translation_pr_number=99,
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=74),
        config=cfg,
    )
    assert "Translation PR | #99" in body
    assert "перевод готов" in body


def test_build_full_report():
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    body = build_full_report(
        _sample_result(),
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=90),
        config=cfg,
    )
    assert "отчёт #1" in body
    assert "ydb/docs/en/a.md" in body
    assert "Prompt version: v1" in body


def test_build_commit_message():
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    msg = build_commit_message(12, _sample_result(), config=cfg)
    assert "PR #12" in msg
    verify_msg = build_commit_message(12, _sample_result(), config=cfg, verify=True)
    assert "doc_verify" in verify_msg
