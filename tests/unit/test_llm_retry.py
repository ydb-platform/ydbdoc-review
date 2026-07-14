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


def test_parse_retry_after_seconds_and_rate_limit_backoff():
    from ydbdoc_review.config.loader import RateLimitRetriesConfig, RetriesConfig
    from ydbdoc_review.llm.retry import (
        compute_rate_limit_backoff_s,
        parse_retry_after_s,
        retry_delay_s,
    )

    assert parse_retry_after_s("5") == 5.0
    rl = RateLimitRetriesConfig(
        max_attempts=6, backoff_initial_s=5.0, backoff_factor=2.0, max_backoff_s=120.0
    )
    assert compute_rate_limit_backoff_s(1, rl) == 5.0
    assert compute_rate_limit_backoff_s(2, rl) == 10.0
    retries = RetriesConfig(rate_limit=rl)
    assert retry_delay_s(
        attempt=1,
        retries=retries,
        status_code=429,
        retry_after_s=7.0,
    ) == 7.0
    assert retry_delay_s(
        attempt=2,
        retries=retries,
        status_code=429,
        retry_after_s=None,
    ) == 10.0


def test_build_eliza_http_error_classifies_retryable_and_fail_fast():
    from ydbdoc_review.llm.errors import LLMRequestError, LLMRetryableRequestError
    from ydbdoc_review.llm.retry import build_eliza_http_error

    retry = build_eliza_http_error(503, "Service unavailable")
    assert isinstance(retry, LLMRetryableRequestError)
    assert retry.status_code == 503

    fail = build_eliza_http_error(401, "OAuth secret-token bad", redact="secret-token")
    assert isinstance(fail, LLMRequestError)
    assert "401" in str(fail)
    assert "secret-token" not in str(fail)
    assert "unauthorized" in str(fail).lower()


def test_is_transient_requests_error():
    import requests

    from ydbdoc_review.llm.retry import is_transient_requests_error

    assert is_transient_requests_error(requests.exceptions.Timeout("t"))
    assert is_transient_requests_error(
        requests.exceptions.ConnectionError("reset")
    )
    ssl_exc = requests.exceptions.SSLError("cert verify failed")
    assert not is_transient_requests_error(ssl_exc)
    assert not is_transient_requests_error(
        requests.exceptions.ConnectionError(ssl_exc)
    )


def _fake_response(status_code: int) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status_code,
        headers={},
        request=SimpleNamespace(),
    )
