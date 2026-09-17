import logging
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.unit.test_translator import _json_response, _segment
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMRetryExhaustedError
from ydbdoc_review.llm.progress import llm_call_heartbeat
from ydbdoc_review.segmentation.chunker import Batch
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.translator import translate_batch


def test_F130_heartbeat(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="ydbdoc_review.llm.progress"):
        with llm_call_heartbeat(role="critic", model="test-model", interval_s=0.01):
            time.sleep(0.03)
    messages = [record.getMessage() for record in caplog.records]
    waiting = [message for message in messages if "LLM still waiting" in message]
    assert waiting
    assert all("role=critic" in message and "model=test-model" in message for message in waiting)
    assert any("LLM call done" in message for message in messages)


def test_F130_stop_or_fallback() -> None:
    client = MagicMock(spec=YandexLLMClient)
    client.model_chain_for_role.return_value = ["primary", "fallback"]
    client.chat.side_effect = [
        LLMRetryExhaustedError("model unavailable after retries"),
        SimpleNamespace(content=_json_response([{"id": "s1", "text": "Hello"}])),
    ]
    reasons: list[str] = []
    result = translate_batch(
        client,
        Batch(index=0, segments=[_segment("s1", "Привет")]),
        load_glossary(),
        file_path="docs/ru/a.md",
        fallback_reasons=reasons,
    )

    assert result == {"s1": "Hello"}
    assert client.chat.call_count == 2
    assert reasons == ["primary -> fallback: model unavailable after retries"]
