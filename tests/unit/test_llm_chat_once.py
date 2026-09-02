from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.client import (
    ElizaLLMClient,
    YandexLLMClient,
    _normalize_yandex_once_error,
)
from ydbdoc_review.llm.errors import (
    ChatOnceFailureKind,
    LLMConfigError,
    LLMError,
    LLMModelUnavailableError,
    LLMProtocolResponseError,
    LLMRequestError,
    LLMRetryableRequestError,
)
from ydbdoc_review.llm.retry import is_model_unavailable, is_retryable


def _completion(content: str | None = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
    )


def _yandex() -> tuple[YandexLLMClient, MagicMock]:
    transport = MagicMock()
    client = YandexLLMClient(
        folder_id="folder",
        api_key="key",
        llm=load_config(env={}).llm,
        client=transport,
    )
    return client, transport


def _response(status: int, payload: object, *, retry_after: str | None = None):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return SimpleNamespace(
        status_code=status,
        text=str(payload),
        headers=headers,
        json=lambda: payload,
    )


class _RawProviderError(Exception):
    def __init__(self, message, *, status=None, code=None, payload=None):
        super().__init__(message)
        self.status_code = status
        self.code = code
        self.response = None if payload is None else _response(status, payload)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (_RawProviderError("gone", status=404, code="MODEL_NOT_FOUND"), LLMModelUnavailableError),
        (_RawProviderError("gone", status=400, code="MODEL_UNAVAILABLE"), LLMModelUnavailableError),
        (_RawProviderError("denied", status=401, code="MODEL_NOT_FOUND"), LLMRequestError),
        (_RawProviderError("busy", status=429, code="MODEL_NOT_FOUND"), LLMRetryableRequestError),
        (_RawProviderError("down", status=503), LLMRetryableRequestError),
        (_RawProviderError("missing", status=404), LLMRequestError),
        (_RawProviderError("Failed to get model: x", status=404), LLMModelUnavailableError),
        (_RawProviderError("prefix Failed to get model: x", status=404), LLMRequestError),
        (_RawProviderError("Failed to get models: x", status=404), LLMRequestError),
        (_RawProviderError("unknown", status=404, code="SOMETHING_ELSE"), LLMRequestError),
    ],
)
def test_yandex_once_raw_provider_error_priority_table(raw, expected):
    normalized = _normalize_yandex_once_error(raw)

    assert type(normalized) is expected


def _eliza() -> ElizaLLMClient:
    return ElizaLLMClient(
        api_root="https://eliza.invalid",
        oauth_token="secret",
        llm=load_config(env={}).llm,
    )


def test_yandex_chat_once_uses_only_explicit_model_and_does_not_mutate_messages():
    client, transport = _yandex()
    transport.chat.completions.create.return_value = _completion()
    recorder = MagicMock()
    client.transcript_recorder = recorder
    messages = [{"role": "user", "content": "text"}]
    original = [dict(messages[0])]

    with patch.object(client, "_model_chain_for_role") as chain:
        result = client.chat_once(
            messages,
            explicit_model="  model-b  ",
            role="repair",
        )

    assert messages == original
    chain.assert_not_called()
    transport.chat.completions.create.assert_called_once()
    assert transport.chat.completions.create.call_args.kwargs["model"] == "gpt://folder/model-b"
    assert result.model_slug == "model-b"
    assert result.usage.retries == 0
    assert result.usage.role == "repair"
    assert len(client.usage_tracker.records) == 1
    recorder.record.assert_called_once()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"explicit_model": " "},
        {"explicit_model": "m", "temperature": -0.1},
        {"explicit_model": "m", "max_tokens": 0},
    ],
)
def test_yandex_chat_once_invalid_arguments_do_not_dispatch(kwargs):
    client, transport = _yandex()

    with pytest.raises(LLMConfigError) as exc_info:
        client.chat_once([{"role": "user", "content": "x"}], role=None, **kwargs)

    assert exc_info.value.chat_once_kind is ChatOnceFailureKind.PERMANENT
    transport.chat.completions.create.assert_not_called()
    assert client.usage_tracker.records == []


def test_yandex_chat_once_protocol_error_is_one_failed_attempt():
    client, transport = _yandex()
    transport.chat.completions.create.return_value = _completion("")

    with pytest.raises(LLMProtocolResponseError) as exc_info:
        client.chat_once([], explicit_model="m", role="translate")

    assert exc_info.value.chat_once_kind is ChatOnceFailureKind.PROTOCOL_INVALID
    transport.chat.completions.create.assert_called_once()
    assert len(client.usage_tracker.records) == 1
    assert client.usage_tracker.records[0].success is False
    assert client.usage_tracker.records[0].retries == 0


@pytest.mark.parametrize(
    "effect,exception_type,kind",
    [
        (
            LLMRetryableRequestError("rate", status_code=429, retry_after_s=2),
            LLMRetryableRequestError,
            ChatOnceFailureKind.TRANSIENT,
        ),
        (
            LLMModelUnavailableError("gone"),
            LLMModelUnavailableError,
            ChatOnceFailureKind.MODEL_UNAVAILABLE,
        ),
        (RuntimeError("bad request"), LLMRequestError, ChatOnceFailureKind.PERMANENT),
    ],
)
def test_yandex_chat_once_failure_is_typed_and_never_retried(effect, exception_type, kind):
    client, transport = _yandex()
    transport.chat.completions.create.side_effect = effect
    with patch("ydbdoc_review.llm.client.interruptible_sleep") as sleep:
        with pytest.raises(exception_type) as exc_info:
            client.chat_once([], explicit_model="m", role="translate")

    transport.chat.completions.create.assert_called_once()
    sleep.assert_not_called()
    assert exc_info.value.chat_once_kind is kind
    assert len(client.usage_tracker.records) == 1


def test_yandex_chat_once_pre_dispatch_cancellation_has_zero_dispatch_and_usage():
    client, transport = _yandex()

    class CancelledMessages(list):
        def __iter__(self):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        client.chat_once(CancelledMessages(), explicit_model="m", role="translate")

    transport.chat.completions.create.assert_not_called()
    assert client.usage_tracker.records == []


def test_yandex_chat_once_telemetry_failures_do_not_replace_success():
    client, transport = _yandex()
    transport.chat.completions.create.return_value = _completion()
    client._usage = MagicMock()
    client._usage.add.side_effect = RuntimeError("metrics down")
    client.transcript_recorder = MagicMock()
    client.transcript_recorder.record.side_effect = RuntimeError("transcript down")

    result = client.chat_once([], explicit_model="m", role=None)

    assert result.content == "ok"
    transport.chat.completions.create.assert_called_once()


def test_yandex_chat_once_cancellation_is_unchanged_and_never_replaced():
    client, transport = _yandex()
    cancellation = asyncio.CancelledError()
    transport.chat.completions.create.side_effect = cancellation

    with pytest.raises(asyncio.CancelledError) as exc_info:
        client.chat_once([], explicit_model="m", role="translate")

    assert exc_info.value is cancellation
    transport.chat.completions.create.assert_called_once()


def test_eliza_chat_once_one_post_preserves_transport_and_uses_no_body_model():
    client = _eliza()
    recorder = MagicMock()
    client.transcript_recorder = recorder
    with patch.object(client._http, "post") as post:
        post.return_value = _response(
            200,
            {
                "choices": [{"message": {"content": "translated"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )
        result = client.chat_once(
            [{"role": "user", "content": "x"}],
            explicit_model=" model-b ",
            role="translate",
        )

    post.assert_called_once()
    args, kwargs = post.call_args
    assert "/model-b/v1/chat/completions" in args[0]
    assert kwargs["headers"]["authorization"] == "OAuth secret"
    assert kwargs["timeout"] == float(client._llm.timeout_s)
    assert "model" not in kwargs["json"]
    assert result.model_slug == "model-b"
    assert result.usage.retries == 0
    recorder.record.assert_called_once()


@pytest.mark.parametrize(
    "effect,expected_kind",
    [
        (requests.exceptions.Timeout("late"), ChatOnceFailureKind.TRANSIENT),
        (requests.exceptions.ConnectionError("reset"), ChatOnceFailureKind.TRANSIENT),
        (requests.exceptions.SSLError("bad cert"), ChatOnceFailureKind.PERMANENT),
    ],
)
def test_eliza_chat_once_transport_failure_dispatches_once(effect, expected_kind):
    client = _eliza()
    with (
        patch.object(client._http, "post", side_effect=effect) as post,
        patch("ydbdoc_review.llm.client.interruptible_sleep") as sleep,
    ):
        with pytest.raises(LLMRequestError) as exc_info:
            client.chat_once([], explicit_model="m", role="translate")

    post.assert_called_once()
    sleep.assert_not_called()
    assert exc_info.value.chat_once_kind is expected_kind
    assert exc_info.value.__cause__ is effect
    assert len(client.usage_tracker.records) == 1


@pytest.mark.parametrize(
    "response,exception_type,kind",
    [
        (
            _response(429, {"error": "rate limit"}, retry_after="3"),
            LLMRetryableRequestError,
            ChatOnceFailureKind.TRANSIENT,
        ),
        (
            _response(429, {"error": "model is overloaded"}),
            LLMModelUnavailableError,
            ChatOnceFailureKind.MODEL_UNAVAILABLE,
        ),
        (_response(401, {"error": "denied"}), LLMRequestError, ChatOnceFailureKind.PERMANENT),
        (_response(404, {"error": "missing"}), LLMRequestError, ChatOnceFailureKind.PERMANENT),
        (
            _response(503, {"error": "down"}),
            LLMRetryableRequestError,
            ChatOnceFailureKind.TRANSIENT,
        ),
        (
            _response(200, {"choices": []}),
            LLMProtocolResponseError,
            ChatOnceFailureKind.PROTOCOL_INVALID,
        ),
    ],
)
def test_eliza_chat_once_http_and_protocol_outcomes_are_typed_once(response, exception_type, kind):
    client = _eliza()
    with (
        patch.object(client._http, "post", return_value=response) as post,
        patch("ydbdoc_review.llm.client.interruptible_sleep") as sleep,
    ):
        with pytest.raises(exception_type) as exc_info:
            client.chat_once([], explicit_model="m", role="critic")

    post.assert_called_once()
    sleep.assert_not_called()
    assert exc_info.value.chat_once_kind is kind
    assert len(client.usage_tracker.records) == 1


def test_model_unavailable_compatibility_golden():
    exc = LLMModelUnavailableError("unavailable")

    assert LLMModelUnavailableError.__mro__ == (
        LLMModelUnavailableError,
        LLMRequestError,
        LLMError,
        Exception,
        BaseException,
        object,
    )
    assert isinstance(exc, LLMRequestError)
    assert not isinstance(exc, LLMRetryableRequestError)
    assert is_model_unavailable(exc)
    assert not is_retryable(exc)
    assert exc.chat_once_kind is ChatOnceFailureKind.MODEL_UNAVAILABLE


@pytest.mark.parametrize("client_type", [YandexLLMClient, ElizaLLMClient])
def test_chat_once_static_shape_has_no_loop_chain_chat_cache_or_repair(client_type):
    tree = ast.parse(textwrap.dedent(inspect.getsource(client_type.chat_once)))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

    assert not any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(tree))
    forbidden = {"chat", "_model_chain_for_role", "interruptible_sleep", "sleep"}
    called_names = {call.func.attr for call in calls if isinstance(call.func, ast.Attribute)}
    assert called_names.isdisjoint(forbidden)
    source = inspect.getsource(client_type.chat_once).lower()
    assert "cache" not in source
    assert "repair" not in source
