"""Retry helpers: backoff timing and error classification."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from ydbdoc_review.config.loader import RateLimitRetriesConfig, RetriesConfig
from ydbdoc_review.llm.errors import LLMModelUnavailableError, LLMRequestError, LLMRetryableRequestError

HTTP_RATE_LIMIT = 429

ELIZA_RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

ELIZA_FAIL_FAST_HTTP_HINTS: dict[int, str] = {
    400: "bad request",
    401: "unauthorized — check ELIZA_OAUTH_TOKEN",
    403: "forbidden",
    404: "not found — check model id in URL path",
}


def is_eliza_retryable_http_status(status_code: int) -> bool:
    return status_code in ELIZA_RETRYABLE_HTTP_STATUSES


def sanitize_llm_error_text(text: str, *, redact: str = "") -> str:
    """Trim and redact secrets from upstream error snippets."""
    import re

    snippet = (text or "").replace("\n", " ").strip()[:200]
    if redact:
        snippet = snippet.replace(redact, "***")
    snippet = re.sub(r"\bOAuth\s+\S+", "OAuth ***", snippet, flags=re.IGNORECASE)
    snippet = re.sub(r"\bBearer\s+\S+", "Bearer ***", snippet, flags=re.IGNORECASE)
    return snippet


def build_eliza_http_error(
    status_code: int,
    body_text: str,
    *,
    redact: str = "",
) -> LLMRetryableRequestError | LLMRequestError:
    """Classify Eliza HTTP status into retryable vs fail-fast errors."""
    detail = sanitize_llm_error_text(body_text, redact=redact)
    if is_eliza_retryable_http_status(status_code):
        return LLMRetryableRequestError(
            f"Eliza HTTP {status_code}: {detail}",
            status_code=status_code,
        )
    hint = ELIZA_FAIL_FAST_HTTP_HINTS.get(status_code, "client error")
    message = f"Eliza HTTP {status_code} ({hint})"
    if detail:
        message = f"{message}: {detail}"
    return LLMRequestError(message)


def is_transient_requests_error(exc: BaseException) -> bool:
    """True for timeout / connection errors worth retrying (not TLS)."""
    try:
        import requests
    except ImportError:
        return False
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        return not is_requests_ssl_error(exc)
    return False


def compute_backoff_s(attempt: int, cfg: RetriesConfig) -> float:
    """Return sleep duration before retry ``attempt`` (1-based)."""
    return cfg.backoff_initial_s * (cfg.backoff_factor ** (attempt - 1))


def compute_rate_limit_backoff_s(
    attempt: int, cfg: RateLimitRetriesConfig
) -> float:
    """Backoff for HTTP 429 when ``Retry-After`` header is absent."""
    delay = cfg.backoff_initial_s * (cfg.backoff_factor ** (attempt - 1))
    return min(delay, cfg.max_backoff_s)


def parse_retry_after_s(header_value: str | None) -> float | None:
    """Parse ``Retry-After`` header (seconds or HTTP-date) into sleep seconds."""
    if not header_value or not str(header_value).strip():
        return None
    raw = str(header_value).strip()
    try:
        seconds = float(raw)
        return max(0.0, seconds)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def retry_delay_s(
    *,
    attempt: int,
    retries: RetriesConfig,
    status_code: int | None = None,
    retry_after_s: float | None = None,
) -> float:
    """Sleep duration before the next retry attempt."""
    if status_code == HTTP_RATE_LIMIT:
        if retry_after_s is not None:
            return min(retry_after_s, retries.rate_limit.max_backoff_s)
        return compute_rate_limit_backoff_s(attempt, retries.rate_limit)
    return compute_backoff_s(attempt, retries)


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
    if isinstance(exc, LLMRetryableRequestError):
        if exc.status_code == HTTP_RATE_LIMIT:
            return True
        if exc.status_code is not None:
            return exc.status_code in (408, 500, 502, 503, 504)
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in (408, 429, 500, 502, 503, 504)
    return False


def is_rate_limit_error(exc: BaseException) -> bool:
    """True when the failure is specifically HTTP 429 / rate-limit."""
    if isinstance(exc, LLMRetryableRequestError) and exc.status_code == HTTP_RATE_LIMIT:
        return True
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code == HTTP_RATE_LIMIT:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "rate-limit" in msg


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
