"""Typed exceptions for the LLM client."""

from __future__ import annotations

from enum import Enum


class ChatOnceFailureKind(str, Enum):  # noqa: UP042 - public contract requires Enum
    """Closed failure classification for a single provider dispatch."""

    TRANSIENT = "transient"
    MODEL_UNAVAILABLE = "model_unavailable"
    PROTOCOL_INVALID = "protocol_invalid"
    PERMANENT = "permanent"


class LLMError(Exception):
    """Base class for LLM client errors."""


class LLMConfigError(LLMError):
    """Missing or invalid configuration (credentials, model chain, etc.)."""

    chat_once_kind = ChatOnceFailureKind.PERMANENT


class LLMRequestError(LLMError):
    """The upstream API rejected or failed to serve the request."""

    chat_once_kind = ChatOnceFailureKind.PERMANENT


class LLMRetryableRequestError(LLMRequestError):
    """Transient HTTP failure — safe to retry (408/429/5xx)."""

    chat_once_kind = ChatOnceFailureKind.TRANSIENT

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

    chat_once_kind = ChatOnceFailureKind.MODEL_UNAVAILABLE


class LLMProtocolResponseError(LLMRequestError):
    """A completed provider response did not contain a valid completion."""

    chat_once_kind = ChatOnceFailureKind.PROTOCOL_INVALID


class LLMRetryExhaustedError(LLMError):
    """All models and retry attempts were exhausted."""


class LLMParseError(LLMError):
    """Response content could not be parsed as expected JSON."""
