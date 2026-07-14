"""Tests for retry helpers."""

from __future__ import annotations

from types import SimpleNamespace

from openai import APIStatusError

from ydbdoc_review.config.loader import RetriesConfig
from ydbdoc_review.llm.errors import LLMModelUnavailableError
from ydbdoc_review.llm.retry import (
    compute_backoff_s,
    is_model_unavailable,
    is_requests_ssl_error,
    is_retryable,
)


def test_compute_backoff_exponential():
    cfg = RetriesConfig(max_attempts=3, backoff_initial_s=2.0, backoff_factor=2.0)
    assert compute_backoff_s(1, cfg) == 2.0
    assert compute_backoff_s(2, cfg) == 4.0
    assert compute_backoff_s(3, cfg) == 8.0


def test_is_model_unavailable_message():
    assert is_model_unavailable(RuntimeError("Failed to get model: foo"))
    assert not is_model_unavailable(RuntimeError("timeout"))


def test_is_model_unavailable_typed():
    assert is_model_unavailable(LLMModelUnavailableError("x"))


def test_is_retryable_rate_limit():
    err = APIStatusError("rate limit", response=_fake_response(429), body=None)
    assert is_retryable(err)


def test_is_retryable_model_unavailable_not_retryable():
    err = LLMModelUnavailableError("Failed to get model")
    assert not is_retryable(err)


def test_is_retryable_500():
    err = APIStatusError("server", response=_fake_response(500), body=None)
    assert is_retryable(err)


def test_is_retryable_400_not_retryable():
    err = APIStatusError("bad", response=_fake_response(400), body=None)
    assert not is_retryable(err)


def test_is_requests_ssl_error_direct_and_wrapped():
    import requests

    ssl_exc = requests.exceptions.SSLError("cert verify failed")
    assert is_requests_ssl_error(ssl_exc)

    conn_exc = requests.exceptions.ConnectionError(ssl_exc)
    assert is_requests_ssl_error(conn_exc)
    assert not is_requests_ssl_error(requests.exceptions.Timeout("timed out"))


def _fake_response(status_code: int) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status_code,
        headers={},
        request=SimpleNamespace(),
    )
