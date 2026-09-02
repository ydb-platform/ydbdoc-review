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
from ydbdoc_review.translation.model_policy import (
    ModelPair,
    TranslationJobManifest,
    TranslationModelPolicy,
)

MANIFEST = TranslationJobManifest(TranslationModelPolicy(
    translate=ModelPair("translate-primary", "translate-fallback"),
    critic=ModelPair("critic-primary", "critic-fallback"),
    repair=ModelPair("repair-primary", "repair-fallback"),
))


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
    critic_raw = json.dumps({"findings": []})

    client = _mock_client([translate_raw, critic_raw])
    cache: dict[str, str] = {}
    result = run_pr_translation(
        [content],
        client,
        load_glossary(),
        use_analyze_llm=False,
        per_pr_cache=cache,
        manifest=MANIFEST,
    )

    assert result.translated_count == 1
    assert result.pair_results[0].target_text is not None
    assert "Hello." in result.pair_results[0].target_text


def test_run_pr_translation_delete_is_non_model_action():
    del_pair = DocPair(
        ru_path="ydb/docs/ru/gone.md",
        en_path="ydb/docs/en/gone.md",
        ru_changed=True,
        ru_deleted=True,
    )
    contents = [PairContent(pair=del_pair)]
    client = _mock_client([])
    result = run_pr_translation(
        contents, client, load_glossary(), use_analyze_llm=False, manifest=MANIFEST
    )

    by_action = {r.plan.action: r for r in result.pair_results}
    assert by_action["delete_en"].deleted


def test_run_pr_translation_uses_universal_ru_action_for_previous_tombstone():
    pair = DocPair(
        ru_path="ydb/docs/ru/core/maintenance/manual/dynamic-config.md",
        en_path="ydb/docs/en/core/maintenance/manual/dynamic-config.md",
        ru_changed=True,
    )
    content = PairContent(
        pair=pair,
        ru_text="# Динамическая конфигурация\n",
        en_text=None,
    )
    client = _mock_client([
        _translate_json("s0001", "# Dynamic configuration"),
        json.dumps({"findings": []}),
    ])
    tombstone = frozenset({pair.en_path})
    result = run_pr_translation(
        [content],
        client,
        load_glossary(),
        use_analyze_llm=False,
        redirect_source_en_paths=tombstone,
        en_toc_reachable=frozenset(),
        manifest=MANIFEST,
    )
    assert result.translated_count == 1
    assert result.failed_count == 0
    assert len(result.pair_results) == 1
    run = result.pair_results[0]
    assert not run.skipped
    assert run.plan.action == "translate_ru_to_en_once"


def test_run_pr_translation_missing_source():
    pair = DocPair(
        ru_path="ydb/docs/ru/missing.md",
        en_path="ydb/docs/en/missing.md",
        ru_changed=True,
    )
    content = PairContent(pair=pair, ru_text=None)
    client = _mock_client([])
    result = run_pr_translation(
        [content], client, load_glossary(), use_analyze_llm=False, manifest=MANIFEST
    )
    assert result.failed_count == 1
    assert result.pair_results[0].error is not None


def test_run_pr_translation_isolates_validation_failure():
    pair = DocPair(
        ru_path="ydb/docs/ru/bad.md",
        en_path="ydb/docs/en/bad.md",
        ru_changed=True,
    )
    content = PairContent(pair=pair, ru_text="⟦C1⟧ and ⟦L1⟧ here.\n")
    # Dropped placeholder — cannot realign (count mismatch).
    bad = _translate_json("s0001", "⟦C1⟧ only")
    client = _mock_client([bad, bad, bad, bad, bad, bad])
    result = run_pr_translation(
        [content],
        client,
        load_glossary(),
        use_analyze_llm=False,
        manifest=MANIFEST,
    )
    assert result.failed_count == 1
    assert result.translated_count == 0
    err = result.pair_results[0].error or ""
    assert "translation_acquisition_exhausted: role=translate" in err
