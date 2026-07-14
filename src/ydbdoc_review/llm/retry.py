"""Retry helpers: backoff timing and error classification."""

from __future__ import annotations

from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from ydbdoc_review.config.loader import RetriesConfig
from ydbdoc_review.llm.errors import LLMModelUnavailableError


def compute_backoff_s(attempt: int, cfg: RetriesConfig) -> float:
    """Return sleep duration before retry ``attempt`` (1-based)."""
    return cfg.backoff_initial_s * (cfg.backoff_factor ** (attempt - 1))


def is_model_unavailable(exc: BaseException) -> bool:
    """True when the API reports the model slug is unavailable in the folder."""
    if isinstance(exc, LLMModelUnavailableError):
        return True
    msg = str(exc).lower()
    return "failed to get model" in msg


def is_retryable(exc: BaseException) -> bool:
    """True for transient errors worth retrying on the same model."""
    if is_model_unavailable(exc):
        return False
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in (408, 429, 500, 502, 503, 504)
    return False


def is_requests_ssl_error(exc: BaseException) -> bool:
    """True for TLS/certificate failures that cannot succeed on retry."""
    try:
        import requests
    except ImportError:
        return False
    ssl_error = requests.exceptions.SSLError
    to_visit: list[BaseException] = [exc]
    seen: set[int] = set()
    while to_visit:
        current = to_visit.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl_error):
            return True
        if current.__cause__ is not None:
            to_visit.append(current.__cause__)
        for arg in getattr(current, "args", ()):
            if isinstance(arg, BaseException):
                to_visit.append(arg)
    return False


def classify_api_error(exc: BaseException) -> BaseException:
    """Wrap raw OpenAI errors with model-unavailable detection."""
    if is_model_unavailable(exc):
        err = LLMModelUnavailableError(str(exc))
        err.__cause__ = exc
        return err
    return exc
