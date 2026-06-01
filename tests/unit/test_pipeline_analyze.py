"""Tests for pre-analyze planning."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.pipeline.analyze import (
    PairContent,
    plan_from_analyze,
    plan_pair_heuristic,
    plan_pairs,
)
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary
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
    assert plan is not None
    assert plan.action == "translate_to_en"


def test_heuristic_delete_en():
    plan = plan_pair_heuristic(
        _content(pair=_pair(ru_deleted=True, ru_changed=True))
    )
    assert plan is not None
    assert plan.action == "delete_en"


def test_heuristic_both_changed_needs_llm():
    assert (
        plan_pair_heuristic(
            _content(
                ru_text="RU",
                en_text="EN",
                pair=_pair(ru_changed=True, en_changed=True),
            )
        )
        is None
    )


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


def _mock_client(response: str) -> YandexLLMClient:
    mock = MagicMock()
    mock.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=response))],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    return YandexLLMClient(folder_id="b1", api_key="k", llm=cfg.llm, client=mock)


def test_plan_pairs_with_analyze_llm():
    content = _content(
        ru_text="RU body",
        en_text="EN body",
        pair=_pair(ru_changed=True, en_changed=True),
    )
    analyze_raw = json.dumps(
        {
            "results": [
                {
                    "ru_path": content.pair.ru_path,
                    "en_path": content.pair.en_path,
                    "ru_present": True,
                    "en_present": True,
                    "semantically_aligned": True,
                    "needs_generation_for": None,
                    "summary": "synced edit",
                }
            ]
        }
    )
    client = _mock_client(analyze_raw)
    plans = plan_pairs([content], client, load_glossary())
    assert len(plans) == 1
    assert plans[0].action == "critic_only"


def test_plan_pairs_without_llm_defaults_ru_to_en():
    content = _content(
        ru_text="RU",
        en_text="EN",
        pair=_pair(ru_changed=True, en_changed=True),
    )
    plans = plan_pairs([content], None, load_glossary(), use_analyze_llm=False)
    assert plans[0].action == "translate_to_en"


def test_heuristic_translate_en_only_changed():
    plan = plan_pair_heuristic(
        _content(en_text="EN only", pair=_pair(en_changed=True, ru_changed=False))
    )
    assert plan is not None
    assert plan.action == "translate_to_ru"


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


def test_plan_pairs_missing_analyze_result_defaults():
    content = _content(
        ru_text="RU",
        en_text="EN",
        pair=_pair(ru_changed=True, en_changed=True),
    )
    analyze_raw = json.dumps({"results": []})
    client = _mock_client(analyze_raw)
    plans = plan_pairs([content], client, load_glossary())
    assert plans[0].action == "translate_to_en"
