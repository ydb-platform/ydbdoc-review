"""Tests for source PR completeness gate."""

from __future__ import annotations

from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.completeness import completeness_gaps, expected_en_mirrors
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    NavigationRunResult,
    PRTranslationResult,
    PairRunResult,
)


def test_expected_en_mirrors_includes_md_and_toc():
    changes = [
        ("ydb/docs/ru/a/compact.md", "added"),
        ("ydb/docs/ru/a/toc_i.yaml", "modified"),
        ("src/not-docs/foo.md", "modified"),
    ]
    expected = expected_en_mirrors(changes)
    assert "ydb/docs/en/a/compact.md" in expected
    assert "ydb/docs/en/a/toc_i.yaml" in expected
    assert len(expected) == 2


def test_completeness_gaps_detects_missing_toc():
    changes = [
        ("ydb/docs/ru/a/compact.md", "added"),
        ("ydb/docs/ru/a/toc_i.yaml", "modified"),
    ]
    pair = DocPair(ru_path="ydb/docs/ru/a/compact.md", en_path="ydb/docs/en/a/compact.md")
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                target_text="# EN\n",
                file_result=FileTranslationResult(
                    file_path=pair.ru_path,
                    final_text="# EN\n",
                    segments_count=1,
                    verdict="ok",
                    prompt_version="v1",
                ),
            )
        ],
    )
    gaps = completeness_gaps(changes, result)
    assert gaps == ["ydb/docs/en/a/toc_i.yaml"]


def test_completeness_ok_when_navigation_merged():
    changes = [
        ("ydb/docs/ru/a/toc_i.yaml", "modified"),
    ]
    result = PRTranslationResult(
        navigation_results=[
            NavigationRunResult(
                ru_path="ydb/docs/ru/a/toc_i.yaml",
                en_path="ydb/docs/en/a/toc_i.yaml",
                kind="toc",
                target_text="items:\n",
            )
        ],
    )
    assert completeness_gaps(changes, result) == []
