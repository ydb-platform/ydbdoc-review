"""Bounded translation-local acquisition over explicit model pairs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from openai.types.chat import ChatCompletionMessageParam

from ydbdoc_review.llm.errors import ChatOnceFailureKind
from ydbdoc_review.translation.model_policy import (
    ModelPair,
    TranslationChatOnce,
)

AcquisitionRole = Literal["translate", "critic", "repair", "navigation"]
AttemptClassification = Literal[
    "accepted",
    "transport_error",
    "model_unavailable",
    "protocol_error",
    "blocking_error",
]
T = TypeVar("T")


class AcquisitionProtocolError(ValueError):
    pass


class AcquisitionBlockedError(RuntimeError):
    def __init__(self, message: str, *, attempts: tuple[AcquisitionAttempt, ...]):
        super().__init__(message)
        self.attempts = attempts


class AcquisitionExhaustedError(RuntimeError):
    def __init__(self, message: str, *, attempts: tuple[AcquisitionAttempt, ...]):
        super().__init__(message)
        self.attempts = attempts


@dataclass(frozen=True)
class AcquisitionAttempt:
    ordinal: int
    role: AcquisitionRole
    model_slug: str
    model_attempt: int
    classification: AttemptClassification
    error: str | None = None


@dataclass(frozen=True)
class AcquisitionResult(Generic[T]):
    payload: T
    model_slug: str
    attempts: tuple[AcquisitionAttempt, ...]


def _safe_error(exc: BaseException) -> str:
    return " ".join(str(exc).replace("\n", " ").split())[:200]


class AcquisitionController(Generic[T]):
    """Apply the exhaustive two-model, four-network-request policy."""

    def __init__(
        self,
        client: TranslationChatOnce,
        model_pair: ModelPair,
        *,
        role: AcquisitionRole,
        parser: Callable[[object], T],
    ) -> None:
        self._client = client
        self._pair = model_pair
        self._role = role
        self._parser = parser

    def acquire(
        self,
        messages: Sequence[ChatCompletionMessageParam],
    ) -> AcquisitionResult[T]:
        # Freeze the request as bytes once. Every attempt receives an equivalent
        # fresh value, so neither caller nor provider adapter can mutate retries.
        request_bytes = json.dumps(
            list(messages), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        attempts: list[AcquisitionAttempt] = []
        for model_slug in (self._pair.primary, self._pair.fallback):
            for model_attempt in (1, 2):
                request = json.loads(request_bytes.decode("utf-8"))
                try:
                    response = self._client.chat_once(
                        request,
                        explicit_model=model_slug,
                        role=self._role,
                    )
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    kind = getattr(exc, "chat_once_kind", None)
                    if kind is ChatOnceFailureKind.MODEL_UNAVAILABLE:
                        attempts.append(self._attempt(attempts, model_slug, model_attempt, "model_unavailable", exc))
                        break
                    if kind is ChatOnceFailureKind.TRANSIENT:
                        attempts.append(self._attempt(attempts, model_slug, model_attempt, "transport_error", exc))
                        if model_attempt == 1:
                            continue
                        break
                    if kind is ChatOnceFailureKind.PROTOCOL_INVALID:
                        attempts.append(self._attempt(attempts, model_slug, model_attempt, "protocol_error", exc))
                        break
                    attempts.append(self._attempt(attempts, model_slug, model_attempt, "blocking_error", exc))
                    reason = "permanent" if kind is ChatOnceFailureKind.PERMANENT else "unknown"
                    raise AcquisitionBlockedError(
                        f"{reason} acquisition error for {self._role}: {_safe_error(exc)}",
                        attempts=tuple(attempts),
                    ) from exc

                try:
                    payload = self._parser(response)
                except (AcquisitionProtocolError, ValueError, TypeError, KeyError) as exc:
                    attempts.append(self._attempt(attempts, model_slug, model_attempt, "protocol_error", exc))
                    break
                attempts.append(self._attempt(attempts, model_slug, model_attempt, "accepted", None))
                return AcquisitionResult(payload, model_slug, tuple(attempts))

        raise AcquisitionExhaustedError(
            f"translation_acquisition_exhausted: role={self._role}",
            attempts=tuple(attempts),
        )

    def _attempt(
        self,
        previous: list[AcquisitionAttempt],
        model_slug: str,
        model_attempt: int,
        classification: AttemptClassification,
        error: BaseException | None,
    ) -> AcquisitionAttempt:
        return AcquisitionAttempt(
            ordinal=len(previous) + 1,
            role=self._role,
            model_slug=model_slug,
            model_attempt=model_attempt,
            classification=classification,
            error=None if error is None else _safe_error(error),
        )
