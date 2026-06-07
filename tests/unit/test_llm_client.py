"""Tests for YandexLLMClient (mocked OpenAI SDK)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from openai import APIStatusError

from ydbdoc_review.config.loader import (
    LLMConfig,
    ModelChoice,
    ModelsConfig,
    RetriesConfig,
    load_config,
)
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMConfigError, LLMRetryExhaustedError


def _llm_config(**overrides: object) -> LLMConfig:
    base = load_config(env={}).llm
    data = base.model_dump()
    data.update(overrides)
    return LLMConfig.model_validate(data)


def _completion(
    content: str | None,
    *,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    finish_reason: str = "stop",
    completion_id: str = "cmpl-test",
):
    return SimpleNamespace(
        id=completion_id,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + (completion_tokens or 0),
        ),
    )


def _client_with_mock(llm: LLMConfig | None = None) -> tuple[YandexLLMClient, MagicMock]:
    mock_openai = MagicMock()
    llm_cfg = llm or _llm_config()
    client = YandexLLMClient(
        folder_id="b1test",
        api_key="AQVN_test",
        llm=llm_cfg,
        client=mock_openai,
    )
    return client, mock_openai


def test_model_uri():
    client, _ = _client_with_mock()
    assert client.model_uri("yandexgpt-5.1") == "gpt://b1test/yandexgpt-5.1"


def test_from_config():
    cfg = load_config(
        env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "AQVN_x"}
    )
    client = YandexLLMClient.from_config(cfg)
    assert client.model_uri("yandexgpt-5.1") == "gpt://b1x/yandexgpt-5.1"


def test_chat_success_records_usage():
    client, mock = _client_with_mock()
    mock.chat.completions.create.return_value = _completion("hello", prompt_tokens=12, completion_tokens=3)

    result = client.chat(
        [{"role": "user", "content": "hi"}],
        model="yandexgpt-5.1",
    )

    assert result.content == "hello"
    assert result.model_slug == "yandexgpt-5.1"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 3
    assert result.usage.success is True
    assert len(client.usage_tracker.records) == 1


def test_chat_success_null_completion_tokens():
    client, mock = _client_with_mock()
    mock.chat.completions.create.return_value = _completion(
        "hello", prompt_tokens=12, completion_tokens=None  # type: ignore[arg-type]
    )

    result = client.chat(
        [{"role": "user", "content": "hi"}],
        model="yandexgpt-5.1",
    )

    assert result.usage.output_tokens == 0
    assert client.usage_tracker.estimate_cost_usd() >= 0


def test_chat_uses_role_chain():
    client, mock = _client_with_mock()
    mock.chat.completions.create.return_value = _completion("ok")

    client.chat([{"role": "user", "content": "x"}], role="translate")

    call_kwargs = mock.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt://b1test/deepseek-v32"


def test_empty_completion_logs_diagnostics(caplog: pytest.LogCaptureFixture):
    client, mock = _client_with_mock()
    mock.chat.completions.create.return_value = _completion(
        None,
        prompt_tokens=18000,
        completion_tokens=0,
        finish_reason="content_filter",
        completion_id="cmpl-empty-1",
    )

    with caplog.at_level(logging.WARNING, logger="ydbdoc_review.llm.client"):
        result = client.chat(
            [{"role": "user", "content": "x" * 5000}],
            role="critic",
        )

    assert result.content == ""
    joined = caplog.text
    assert "Empty LLM completion" in joined
    assert "content_filter" in joined
    assert "cmpl-empty-1" in joined
    assert "role=critic" in joined
    assert "usage_prompt=18000" in joined
    assert "request_chars=5000" in joined


def test_chat_model_fallback_on_unavailable():
    llm = _llm_config(
        models=ModelsConfig(
            analyze=ModelChoice(primary="missing-model", fallbacks=[]),
            translate=ModelChoice(primary="missing-model", fallbacks=["yandexgpt-5-pro"]),
            critic=ModelChoice(primary="q", fallbacks=[]),
        ),
        retries=RetriesConfig(max_attempts=1, backoff_initial_s=0.01, backoff_factor=1.0),
    )
    client, mock = _client_with_mock(llm)
    mock.chat.completions.create.side_effect = [
        RuntimeError("Failed to get model: missing-model"),
        _completion("fallback ok"),
    ]

    result = client.chat([{"role": "user", "content": "x"}], role="translate")

    assert result.content == "fallback ok"
    assert result.model_slug == "yandexgpt-5-pro"
    assert mock.chat.completions.create.call_count == 2


def test_chat_retries_transient_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    llm = _llm_config(retries=RetriesConfig(max_attempts=3, backoff_initial_s=0.0, backoff_factor=1.0))
    client, mock = _client_with_mock(llm)
    err = APIStatusError("server", response=_fake_response(503), body=None)
    mock.chat.completions.create.side_effect = [err, _completion("recovered")]
    sleeps: list[float] = []
    monkeypatch.setattr("ydbdoc_review.llm.client.time.sleep", lambda s: sleeps.append(s))

    result = client.chat([{"role": "user", "content": "x"}], model="yandexgpt-5.1")

    assert result.content == "recovered"
    assert mock.chat.completions.create.call_count == 2
    assert result.usage.retries == 1


def test_chat_exhausted_raises():
    llm = _llm_config(
        models=ModelsConfig(
            analyze=ModelChoice(primary="a", fallbacks=[]),
            translate=ModelChoice(primary="bad", fallbacks=[]),
            critic=ModelChoice(primary="c", fallbacks=[]),
        ),
        retries=RetriesConfig(max_attempts=1, backoff_initial_s=0.0, backoff_factor=1.0),
    )
    client, mock = _client_with_mock(llm)
    mock.chat.completions.create.side_effect = RuntimeError("Failed to get model")

    with pytest.raises(LLMRetryExhaustedError, match="All models exhausted"):
        client.chat([{"role": "user", "content": "x"}], role="translate")


def test_chat_requires_role_or_model():
    client, _ = _client_with_mock()
    with pytest.raises(LLMConfigError, match="role= or model="):
        client.chat([{"role": "user", "content": "x"}])


def test_chat_model_with_role_tags_usage():
    client, mock = _client_with_mock()
    mock.chat.completions.create.return_value = _completion("hello")
    client.chat(
        [{"role": "user", "content": "x"}],
        model="deepseek-v32",
        role="translate",
    )
    record = client.usage_tracker.records[-1]
    assert record.model_slug == "deepseek-v32"
    assert record.role == "translate"


def test_init_requires_credentials():
    with pytest.raises(LLMConfigError, match="folder_id and api_key"):
        YandexLLMClient(folder_id="", api_key="k", llm=_llm_config())


def _fake_response(status_code: int) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status_code,
        headers={},
        request=SimpleNamespace(),
    )
