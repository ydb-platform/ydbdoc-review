"""Opt-in real-client regression for historical PR #37673."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parents[1] / "fixtures" / "pr_37673_bulk_upsert"
RU_PATH = "ydb/docs/ru/core/recipes/ydb-sdk/bulk-upsert.md"
EN_PATH = "ydb/docs/en/core/recipes/ydb-sdk/bulk-upsert.md"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.skipif(
    os.environ.get("YDBDOC_RUN_LIVE_37673") != "1",
    reason="set YDBDOC_RUN_LIVE_37673=1 to run the real-client regression",
)
def test_live_merged_pr_37673_preserves_current_en_without_translate_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    pair = DocPair(ru_path=RU_PATH, en_path=EN_PATH, ru_changed=True)
    content = PairContent(
        pair=pair,
        ru_text=_read("ru_after.md"),
        en_text=_read("en_current.md"),
        ru_base_text=_read("ru_before.md"),
        en_base_text=_read("en_current.md"),
    )
    plan = PairPlan(pair, "translate_to_en", RU_PATH, EN_PATH, "ru", "en")
    ctx = HarnessContext.from_options(
        client,
        glossary=load_glossary(),
        config=config,
        source_lang="ru",
        target_lang="en",
    )

    result = run_pair_plan(
        content, plan, ctx, {}, historical_merged_provenance=True
    )
    expected = _read("expected_en.md")

    assert result.error is None
    assert result.target_text == expected
    assert hashlib.sha256(result.target_text.encode()).hexdigest() == (
        "34472c5c6daaf43f730fee98d190d1ebbfc5af234fd37c67246eacd9039581db"
    )
    assert result.target_text.count("{% list tabs %}") == 5
    assert sum(
        line.lstrip().startswith(("```", "~~~"))
        for line in result.target_text.splitlines()
    ) == 24
    assert translate_calls == 0
    assert not client.usage_tracker.records
