"""Tests for PR-level orchestrator."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
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


def test_run_pr_translation_sequential():
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
        en_changed=False,
    )
    content = PairContent(pair=pair, ru_text="Привет.\n", en_text=None)
    translate_raw = _translate_json("s0001", "Hello.")
    critic_raw = json.dumps({"verdict": "ok", "issues": []})

    client = _mock_client([translate_raw, critic_raw])
    cache: dict[str, str] = {}
    result = run_pr_translation(
        [content],
        client,
        load_glossary(),
        use_analyze_llm=False,
        per_pr_cache=cache,
    )

    assert result.translated_count == 1
    assert result.pair_results[0].target_text is not None
    assert "Hello." in result.pair_results[0].target_text
    assert len(cache) >= 1


def test_run_pr_translation_skip_and_delete():
    skip_pair = DocPair(
        ru_path="ydb/docs/ru/skip.md",
        en_path="ydb/docs/en/skip.md",
    )
    del_pair = DocPair(
        ru_path="ydb/docs/ru/gone.md",
        en_path="ydb/docs/en/gone.md",
        ru_changed=True,
        ru_deleted=True,
    )
    contents = [
        PairContent(pair=skip_pair, ru_text="x", en_text="y"),
        PairContent(pair=del_pair),
    ]
    client = _mock_client([])
    result = run_pr_translation(contents, client, load_glossary(), use_analyze_llm=False)

    by_action = {r.plan.action: r for r in result.pair_results}
    assert by_action["skip"].skipped
    assert by_action["delete_en"].deleted
def test_run_pr_translation_missing_source():
    pair = DocPair(
        ru_path="ydb/docs/ru/missing.md",
        en_path="ydb/docs/en/missing.md",
        ru_changed=True,
    )
    content = PairContent(pair=pair, ru_text=None)
    client = _mock_client([])
    result = run_pr_translation([content], client, load_glossary(), use_analyze_llm=False)
    assert result.failed_count == 1
    assert result.pair_results[0].error is not None
