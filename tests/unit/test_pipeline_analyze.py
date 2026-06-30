"""Tests for pre-analyze planning."""

from __future__ import annotations

import pytest

from ydbdoc_review.pipeline.analyze import (
    PairContent,
    plan_from_analyze,
    plan_pair_heuristic,
    plan_pairs,
)
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.schemas import AnalyzePairResult


def _pair(**kwargs: object) -> DocPair:
    defaults = {"ru_path": "ydb/docs/ru/a.md", "en_path": "ydb/docs/en/a.md"}
    defaults.update(kwargs)
    return DocPair(**defaults)  # type: ignore[arg-type]


def _content(**kwargs: object) -> PairContent:
    pair = kwargs.pop("pair", _pair())
    return PairContent(pair=pair, **kwargs)  # type: ignore[arg-type]


def test_heuristic_translate_ru_only_changed():
    plan = plan_pair_heuristic(
        _content(ru_text="RU", en_text="EN", pair=_pair(ru_changed=True, en_changed=False))
    )
    assert plan.action == "translate_to_en"
    assert plan.source_path.endswith("/ru/a.md")


def test_heuristic_delete_en():
    plan = plan_pair_heuristic(
        _content(pair=_pair(ru_deleted=True, ru_changed=True))
    )
    assert plan.action == "delete_en"


def test_heuristic_both_changed_skip_bilingual():
    plan = plan_pair_heuristic(
        _content(
            ru_text="RU",
            en_text="EN",
            pair=_pair(ru_changed=True, en_changed=True),
        )
    )
    assert plan.action == "skip"
    assert "§6.76" in plan.summary


def test_plan_from_analyze_critic_only():
    result = AnalyzePairResult(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_present=True,
        en_present=True,
        semantically_aligned=True,
        needs_generation_for=None,
        summary="aligned",
    )
    plan = plan_from_analyze(_content(), result)
    assert plan.action == "critic_only"


def test_plan_pairs_skip_when_both_changed():
    content = _content(
        ru_text="RU body",
        en_text="EN body",
        pair=_pair(ru_changed=True, en_changed=True),
    )
    plans = plan_pairs([content])
    assert len(plans) == 1
    assert plans[0].action == "skip"


def test_plan_pairs_rejects_analyze_llm():
    content = _content(
        ru_text="RU",
        en_text="EN",
        pair=_pair(ru_changed=True, en_changed=True),
    )
    with pytest.raises(ValueError, match="use_analyze_llm"):
        plan_pairs([content], use_analyze_llm=True)


def test_heuristic_translate_en_only_changed():
    plan = plan_pair_heuristic(
        _content(en_text="EN only", pair=_pair(en_changed=True, ru_changed=False))
    )
    assert plan.action == "translate_to_ru"
    assert plan.source_path.endswith("/en/a.md")


def test_plan_from_analyze_translate_en():
    result = AnalyzePairResult(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_present=True,
        en_present=False,
        semantically_aligned=False,
        needs_generation_for="en",
        summary="need en",
    )
    plan = plan_from_analyze(_content(), result)
    assert plan.action == "translate_to_en"


def test_heuristic_both_changed_en_only_text():
    plan = plan_pair_heuristic(
        _content(
            en_text="EN only",
            pair=_pair(ru_changed=True, en_changed=True),
        )
    )
    assert plan.action == "skip"
    assert "§6.76" in plan.summary


def test_heuristic_skip_when_unchanged():
    plan = plan_pair_heuristic(
        _content(ru_text="RU", en_text="EN", pair=_pair(ru_changed=False, en_changed=False))
    )
    assert plan.action == "skip"
