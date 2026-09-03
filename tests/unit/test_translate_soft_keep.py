"""Soft-keep translate failure must surface as warning, not silent ok (#52077)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.harness.pr_context import PRHarnessContext
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.errors import TranslationError


def test_translate_soft_keep_sets_warning_not_error():
    pair = DocPair(
        ru_path="ydb/docs/ru/core/reference/configuration/auth_config.md",
        en_path="ydb/docs/en/core/reference/configuration/auth_config.md",
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
    content = PairContent(
        pair=pair,
        ru_text="# RU\n",
        en_text="# EN tip\n",
        en_base_text="# EN tip\n",
    )
    ctx = PRHarnessContext.from_options(
        MagicMock(),
        glossary=MagicMock(),
        config=MagicMock(),
    )
    with patch(
        "ydbdoc_review.harness.pair.FileHarness.run",
        side_effect=TranslationError("Invalid JSON in LLM response"),
    ):
        result = run_pair_plan(content, plan, ctx, cache={})
    assert result.error is None
    assert result.target_text == "# EN tip\n"
    assert result.file_result is not None
    assert result.file_result.verdict == "warnings"
    assert any(
        str(w).startswith("translate_soft_keep:")
        for w in result.file_result.heuristic_warnings
    )
