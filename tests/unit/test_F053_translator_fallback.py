"""F-053: translator fallback is ordered and explains every transition."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.unit.test_translator import _json_response, _segment
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMRetryExhaustedError
from ydbdoc_review.segmentation.chunker import Batch
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.translator import translate_batch


def _client(side_effect) -> MagicMock:
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary", "fallback"]
    client.chat.side_effect = side_effect
    return client


@pytest.mark.parametrize(
    ("primary_response", "expected_reason"),
    [
        ("I can't discuss this request because of content policy.", "model refusal"),
        ("", "empty model response"),
        ("not readable JSON", "unreadable model response"),
        (_json_response([{"id": "other", "text": "wrong"}]), "incomplete model response"),
    ],
)
def test_F053_fallback_causes(primary_response: str, expected_reason: str) -> None:
    seg = _segment("s1", "Привет")
    good = _json_response([{"id": "s1", "text": "Hello"}])

    def chat(*_args, **kwargs):
        if kwargs.get("model") == "primary":
            return SimpleNamespace(content=primary_response)
        return SimpleNamespace(content=good)

    reasons: list[str] = []
    out = translate_batch(
        _client(chat),
        Batch(index=0, segments=[seg]),
        load_glossary(),
        file_path="docs/ru/x.md",
        fallback_reasons=reasons,
    )

    assert out == {"s1": "Hello"}
    assert reasons == [f"primary -> fallback: {expected_reason}"]


def test_F053_unavailable_after_retries_uses_fallback_and_reports_reason() -> None:
    seg = _segment("s1", "Привет")
    good = _json_response([{"id": "s1", "text": "Hello"}])
    exhausted = LLMRetryExhaustedError("model unavailable after retries")

    def chat(*_args, **kwargs):
        if kwargs.get("model") == "primary":
            raise exhausted
        return SimpleNamespace(content=good)

    reasons: list[str] = []
    out = translate_batch(
        _client(chat),
        Batch(index=0, segments=[seg]),
        load_glossary(),
        file_path="docs/ru/x.md",
        fallback_reasons=reasons,
    )

    assert out == {"s1": "Hello"}
    assert reasons == ["primary -> fallback: model unavailable after retries"]


def test_F053_primary_success_does_not_call_fallback() -> None:
    seg = _segment("s1", "Привет")
    client = _client(
        lambda *_args, **_kwargs: SimpleNamespace(
            content=_json_response([{"id": "s1", "text": "Hello"}])
        )
    )
    reasons: list[str] = []

    out = translate_batch(
        client,
        Batch(index=0, segments=[seg]),
        load_glossary(),
        file_path="docs/ru/x.md",
        fallback_reasons=reasons,
    )

    assert out == {"s1": "Hello"}
    assert [call.kwargs["model"] for call in client.chat.call_args_list] == [
        "primary"
    ]
    assert reasons == []
