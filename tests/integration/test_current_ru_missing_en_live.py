"""Opt-in live proof that a current RU page without EN performs translation."""

from __future__ import annotations

import os

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.pipeline.analyze import PairContent, PairProvenance, plan_pair_heuristic
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.validation.hard_file_validator import validate_whole_file

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.environ.get("YDBDOC_RUN_LIVE_CURRENT_MISSING_EN") != "1",
    reason="set YDBDOC_RUN_LIVE_CURRENT_MISSING_EN=1",
)
def test_live_current_ru_missing_en_calls_translator(monkeypatch: pytest.MonkeyPatch):
    config = load_config()
    if not config.secrets.yc_folder_id or not config.secrets.yc_api_key:
        pytest.skip("Yandex credentials are not configured")
    client = YandexLLMClient.from_config(config)
    real_chat = client.chat
    translate_calls = 0

    def counted_chat(*args, **kwargs):
        nonlocal translate_calls
        if kwargs.get("role") == "translate":
            translate_calls += 1
        return real_chat(*args, **kwargs)

    monkeypatch.setattr(client, "chat", counted_chat)
    ru = "# Параметризованные запросы {#parameterized}\n\nИспользуйте параметры.\n"
    pair = DocPair("ydb/docs/ru/core/new.md", "ydb/docs/en/core/new.md", ru_changed=True)
    content = PairContent(
        pair=pair,
        ru_text=ru,
        provenance=PairProvenance.CURRENT_RU_MISSING_EN,
        current_ru_text=ru,
    )
    plan = plan_pair_heuristic(content)
    ctx = HarnessContext.from_options(
        client, glossary=load_glossary(), config=config, enable_critic=False
    )

    result = run_pair_plan(content, plan, ctx, {})

    assert result.error is None
    assert result.target_text
    assert translate_calls > 0
    assert client.usage_tracker.records
    assert client.usage_tracker.total_input_tokens > 0
    assert client.usage_tracker.total_output_tokens > 0
    assert validate_whole_file(
        path=pair.en_path, authoritative_ru=ru, candidate_en=result.target_text
    ) == []
