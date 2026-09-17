"""F-138 contracts for mandatory work around the Analyze advisory result."""

from __future__ import annotations

from ydbdoc_review.pipeline.analyze import PairContent, plan_from_analyze
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.schemas import AnalyzePairResult


def _noop_result(*, ru_present: bool = True, en_present: bool = True):
    return AnalyzePairResult(
        ru_path="ru/a.md",
        en_path="en/a.md",
        ru_present=ru_present,
        en_present=en_present,
        semantically_aligned=True,
        needs_generation_for=None,
        summary="looks aligned",
    )


def test_F138_forced_translation() -> None:
    missing_en = PairContent(
        pair=DocPair("ru/a.md", "en/a.md", ru_changed=True),
        ru_text="Проза источника",
        en_text=None,
    )
    plan = plan_from_analyze(missing_en, _noop_result(en_present=False))
    assert plan.action == "translate_to_en"
    assert plan.source_path == "ru/a.md"
    assert plan.target_path == "en/a.md"

    missing_ru = PairContent(
        pair=DocPair("ru/a.md", "en/a.md", en_changed=True),
        ru_text=None,
        en_text="Source prose",
    )
    plan = plan_from_analyze(missing_ru, _noop_result(ru_present=False))
    assert plan.action == "translate_to_ru"


def test_F138_mechanics_exclusions() -> None:
    # An aligned prose answer remains a critic-only decision, leaving the
    # navigation/link/redirect/assets stages to their dedicated validators.
    aligned = PairContent(
        pair=DocPair("ru/a.md", "en/a.md", ru_changed=True),
        ru_text="Проза",
        en_text="Prose",
    )
    plan = plan_from_analyze(aligned, _noop_result())
    assert plan.action == "critic_only"
    assert plan.summary == "looks aligned"
