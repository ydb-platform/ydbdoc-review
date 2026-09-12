"""F-136 contracts for the independent pre-translation Analyze role."""

from __future__ import annotations

from types import SimpleNamespace

from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.pipeline.analyze import (
    PairContent,
    plan_pair_heuristic,
    run_analyze_batch,
)
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.translation.glossary import load_glossary


def _client(*, analyze: list[str], translate: list[str], critic: list[str]):
    client = object.__new__(YandexLLMClient)
    client._llm = SimpleNamespace(
        models=SimpleNamespace(
            analyze=SimpleNamespace(chain=analyze),
            translate=SimpleNamespace(chain=translate),
            critic=SimpleNamespace(chain=critic),
        )
    )
    return client


def test_F136_role_order() -> None:
    client = _client(
        analyze=["cheap-analyze", "cheap-fallback"],
        translate=["translator", "translator-fallback"],
        critic=["critic"],
    )

    assert client.model_chain_for_role("analyze") == [
        "cheap-analyze",
        "cheap-fallback",
    ]
    assert client.model_chain_for_role("translate") == [
        "translator",
        "translator-fallback",
    ]
    assert client.model_chain_for_role("critic") == ["critic"]

    calls: list[str] = []

    class RecordingClient:
        def chat(self, messages, *, role):
            calls.append(role)
            return SimpleNamespace(
                content=(
                    '{"results": [{"ru_path": "ru/a.md", '
                    '"en_path": "en/a.md", "ru_present": true, '
                    '"en_present": true, "semantically_aligned": true, '
                    '"needs_generation_for": null, "summary": "aligned"}]}'
                )
            )

    result = run_analyze_batch(
        RecordingClient(),
        [
            PairContent(
                pair=DocPair("ru/a.md", "en/a.md", ru_changed=True),
                ru_text="Привет",
                en_text="Hello",
            )
        ],
        load_glossary(),
    )

    assert len(result.results) == 1
    assert calls == ["analyze"]


def test_F136_no_comparison() -> None:
    plan = plan_pair_heuristic(
        PairContent(
            pair=DocPair("ru/mechanical.md", "en/mechanical.md"),
            ru_text="",
            en_text="",
        )
    )

    assert plan.action == "skip"
    assert plan.summary == "Both sides empty"

    missing_target = plan_pair_heuristic(
        PairContent(
            pair=DocPair("ru/new.md", "en/new.md", ru_changed=True),
            ru_text="Новый текст",
            en_text=None,
        )
    )
    assert missing_target.action == "translate_to_en"
    assert missing_target.target_path == "en/new.md"
