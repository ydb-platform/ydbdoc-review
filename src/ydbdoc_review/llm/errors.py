"""Typed exceptions for the LLM client."""

from __future__ import annotations


class LLMError(Exception):
    """Base class for LLM client errors."""


class LLMConfigError(LLMError):
    """Missing or invalid configuration (credentials, model chain, etc.)."""


class LLMRequestError(LLMError):
    """The upstream API rejected or failed to serve the request."""


class LLMRetryableRequestError(LLMRequestError):
    """Transient HTTP failure — safe to retry (408/429/5xx)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_s = retry_after_s


class LLMModelUnavailableError(LLMRequestError):
    """Model slug is not available in the folder (``Failed to get model``)."""


class LLMRetryExhaustedError(LLMError):
    """All models and retry attempts were exhausted."""


class LLMParseError(LLMError):
    """Response content could not be parsed as expected JSON."""
