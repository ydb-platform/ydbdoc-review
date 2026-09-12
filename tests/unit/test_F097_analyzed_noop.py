"""F-097: analyzed no-op boundaries remain side-effect free."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import _pr_result_for_bilingual_skips
from ydbdoc_review.pipeline.analyze import PairContent, plan_from_analyze, plan_pairs
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PairRunResult
from ydbdoc_review.reporting.builder import ReportMeta, build_source_pr_comment
from ydbdoc_review.translation.schemas import AnalyzePairResult


def _content() -> PairContent:
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
        en_changed=True,
    )
    return PairContent(pair=pair, ru_text="RU\n", en_text="EN\n")


def test_F097_early_exit() -> None:
    """An aligned Analyze result is represented without translation artifacts."""
    analyzed = AnalyzePairResult(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_present=True,
        en_present=True,
        semantically_aligned=True,
        needs_generation_for=None,
        summary="RU and EN are already aligned",
    )
    plan = plan_from_analyze(_content(), analyzed)
    result = _pr_result_for_bilingual_skips({plan.target_path}, docs_root="ydb/docs")
    result.pair_results = [PairRunResult(plan=plan, skipped=True)]

    translator = MagicMock()
    heavy_qa = MagicMock()
    assert plan.action == "critic_only"
    assert result.translated_count == 0
    assert result.navigation_results == []
    assert result.publication_failure is None
    translator.assert_not_called()
    heavy_qa.assert_not_called()


def test_F097_comments_scope() -> None:
    """Only an explicit aligned pair gets the no-translation source comment."""
    aligned = AnalyzePairResult(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_present=True,
        en_present=True,
        semantically_aligned=True,
        needs_generation_for=None,
        summary="aligned",
    )
    unsupported = AnalyzePairResult(
        ru_path="ydb/docs/ru/b.md",
        en_path="ydb/docs/en/b.md",
        ru_present=False,
        en_present=True,
        semantically_aligned=False,
        needs_generation_for=None,
        summary="unsupported pair",
    )
    aligned_plan = plan_from_analyze(_content(), aligned)
    unsupported_content = PairContent(
        pair=DocPair(
            ru_path="ydb/docs/ru/b.md",
            en_path="ydb/docs/en/b.md",
            ru_changed=True,
            en_changed=True,
        ),
        ru_text=None,
        en_text="EN\n",
    )
    unsupported_plan = plan_from_analyze(unsupported_content, unsupported)

    assert aligned_plan.action == "critic_only"
    assert unsupported_plan.action == "skip"
    assert "unsupported" not in aligned_plan.summary.lower()
    assert "unsupported" in unsupported_plan.summary.lower()

    result = _pr_result_for_bilingual_skips(
        {aligned_plan.target_path}, docs_root="ydb/docs"
    )
    comment = build_source_pr_comment(
        result,
        translation_pr_number=None,
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=0.0),
        config=load_config(env={"YDBDOC_MODEL_PROVIDER": "yandex_cloud"}),
        committed=False,
    )
    assert "перевод не требуется" in comment
    assert aligned_plan.target_path not in comment
    assert unsupported_plan.target_path not in comment


def test_F097_supported_workflow_does_not_enable_deprecated_analyze_path() -> None:
    """The supported planner cannot silently route unsupported pairs as aligned."""
    with pytest.raises(ValueError, match="use_analyze_llm"):
        plan_pairs([_content()], use_analyze_llm=True)
