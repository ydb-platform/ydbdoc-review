"""F-029: every changed Markdown pair reaches planning without diff thresholds."""

from __future__ import annotations

from ydbdoc_review.pipeline.analyze import PairContent, plan_pairs
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.differential import (
    DifferentialTranslationAnalyzer,
    DifferentialTranslationConfig,
)


def _changed_pair() -> PairContent:
    return PairContent(
        pair=DocPair(
            ru_path="ydb/docs/ru/core/page.md",
            en_path="ydb/docs/en/core/page.md",
            ru_changed=True,
            en_changed=False,
        ),
        ru_text="## Source heading\n\nOne changed source line.\n",
        en_text="## Heading\n\nOne translated line.\n",
    )


def test_F029_small_diff() -> None:
    """A tiny source edit is still a complete RU→EN candidate, not a diff skip."""
    plan = plan_pairs([_changed_pair()])[0]

    assert plan.action == "translate_to_en"
    assert plan.source_path == "ydb/docs/ru/core/page.md"
    assert "full re-translate" in plan.summary


def test_F029_full_or_noop() -> None:
    """A semantic no-op may skip differential work, but cannot remove the pair."""
    source = "## Source heading\n\nSource text.\n"
    formatting_only = source.replace("Source text.", "Source text. ")
    strategy = DifferentialTranslationAnalyzer(
        DifferentialTranslationConfig(enabled=True)
    ).analyze_file_state(
        ru_pr_text=formatting_only,
        en_current_text="## Heading\n\nText.\n",
        ru_base_text=source,
    )

    assert strategy.mode == "skip"
    assert strategy.config["semantic_noop"] is True
    assert plan_pairs([_changed_pair()])[0].action == "translate_to_en"
