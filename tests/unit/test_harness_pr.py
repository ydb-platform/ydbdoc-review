"""Tests for PR-level harness (translate / verify profiles)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness import (
    PRHarness,
    PRHarnessContext,
    PRRunState,
    TRANSLATE_PR_PROFILE,
    VERIFY_PR_PROFILE,
)
from ydbdoc_review.harness.pr_steps import PlanVerifyPairsStep
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.orchestrator import run_pr_translation
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary


def _completion(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _mock_client(responses: list[str]) -> YandexLLMClient:
    mock = MagicMock()
    mock.chat.completions.create.side_effect = [_completion(r) for r in responses]
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    return YandexLLMClient(folder_id="b1", api_key="k", llm=cfg.llm, client=mock)


def _translate_json(seg_id: str, text: str) -> str:
    return json.dumps(
        {"segments": [{"id": seg_id, "text": text}]},
        ensure_ascii=False,
    )


def test_translate_pr_profile_steps():
    names = [s.name for s in TRANSLATE_PR_PROFILE.steps]
    assert names == ["plan_translate_pairs", "execute_pair_plans"]


def test_verify_pr_profile_steps():
    names = [s.name for s in VERIFY_PR_PROFILE.steps]
    assert names == ["plan_verify_pairs", "execute_pair_plans"]


def test_plan_verify_pairs_skips_missing_text():
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    state = PRRunState(contents=[PairContent(pair=pair, ru_text="ru", en_text=None)])
    ctx = PRHarnessContext.from_options(_mock_client([]))
    PlanVerifyPairsStep().run(state, ctx)
    assert len(state.plans) == 1
    assert state.plans[0].action == "skip"


def test_pr_harness_translate_matches_orchestrator():
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    content = PairContent(pair=pair, ru_text="Привет.\n", en_text=None)
    responses = [_translate_json("s0001", "Hello."), json.dumps({"verdict": "ok", "issues": []})]
    glossary = load_glossary()
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})

    harness_result = PRHarness(TRANSLATE_PR_PROFILE).run(
        PRRunState(contents=[content]),
        PRHarnessContext.from_options(_mock_client(responses), glossary=glossary, config=cfg),
    )
    orch_result = run_pr_translation(
        [content],
        _mock_client(responses),
        glossary,
        config=cfg,
        use_analyze_llm=False,
    )

    assert harness_result.translated_count == orch_result.translated_count
    assert harness_result.pair_results[0].target_text == orch_result.pair_results[0].target_text
