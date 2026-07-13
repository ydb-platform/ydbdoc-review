"""LLM clients (OpenAI-compatible transports).

Supports two providers behind the same `chat()` interface:

- **Yandex Cloud FM** (default): `YandexLLMClient` (`gpt://<folder>/<model>` URIs)
- **Eliza** (OpenAI-compatible): `ElizaLLMClient` (raw model id, OAuth header)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Literal

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from ydbdoc_review.config.loader import Config, LLMConfig, ModelChoice
from ydbdoc_review.llm.errors import LLMConfigError, LLMRetryExhaustedError
from ydbdoc_review.llm.retry import (
    classify_api_error,
    compute_backoff_s,
    is_model_unavailable,
    is_retryable,
)
from ydbdoc_review.llm.usage import LLMUsage, UsageTracker

logger = logging.getLogger(__name__)

LLMRole = Literal["analyze", "translate", "critic"]


def _message_char_count(messages: list[ChatCompletionMessageParam]) -> tuple[int, int]:
    """Return (message_count, total content characters) for diagnostics."""
    total = 0
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            total += len(content)
    return len(messages), total


def _usage_fields(usage_obj: object | None) -> tuple[int | None, int | None, int | None]:
    if usage_obj is None:
        return None, None, None
    prompt = getattr(usage_obj, "prompt_tokens", None)
    completion = getattr(usage_obj, "completion_tokens", None)
    total = getattr(usage_obj, "total_tokens", None)
    return prompt, completion, total


def _log_empty_completion_diagnostics(
    *,
    slug: str,
    uri: str,
    role: LLMRole | None,
    messages: list[ChatCompletionMessageParam],
    choice: object,
    completion: object,
    usage_obj: object | None,
) -> None:
    """Log API metadata when the model returns no usable text (debugging flaky critic)."""
    finish_reason = getattr(choice, "finish_reason", None)
    message_count, request_chars = _message_char_count(messages)
    prompt_t, completion_t, total_t = _usage_fields(usage_obj)
    completion_id = getattr(completion, "id", None)
    logger.warning(
        "Empty LLM completion: model=%s uri=%s role=%s finish_reason=%s "
        "usage_prompt=%s usage_completion=%s usage_total=%s "
        "request_messages=%s request_chars=%s completion_id=%s",
        slug,
        uri,
        role,
        finish_reason,
        prompt_t,
        completion_t,
        total_t,
        message_count,
        request_chars,
        completion_id,
    )


@dataclass(frozen=True)
class ChatResult:
    """Successful chat completion."""

    content: str
    model_slug: str
    model_uri: str
    usage: LLMUsage


class YandexLLMClient:
    """OpenAI-compatible client for Yandex AI Studio."""

    def __init__(
        self,
        *,
        folder_id: str,
        api_key: str,
        llm: LLMConfig,
        client: OpenAI | None = None,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        if not folder_id or not api_key:
            raise LLMConfigError("folder_id and api_key are required")
        self._folder_id = folder_id
        self._llm = llm
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=llm.base_url,
            timeout=float(llm.timeout_s),
        )
        self._usage = usage_tracker or UsageTracker()

    @classmethod
    def from_config(cls, config: Config) -> YandexLLMClient:
        folder_id, api_key = config.secrets.require_yandex()
        return cls(folder_id=folder_id, api_key=api_key, llm=config.llm)

    @property
    def usage_tracker(self) -> UsageTracker:
        return self._usage

    def model_chain_for_role(self, role: LLMRole) -> list[str]:
        """Return configured model chain for the given role."""
        return list(self._model_chain_for_role(role))

    def model_uri(self, model_slug: str) -> str:
        return f"gpt://{self._folder_id}/{model_slug}"

    def chat(
        self,
        messages: list[ChatCompletionMessageParam],
        *,
        role: LLMRole | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """Call chat completions with retries and model fallback chain."""
        if model is not None:
            chain = [model]
        elif role is not None:
            chain = self._model_chain_for_role(role)
        else:
            raise LLMConfigError("Either role= or model= is required")

        temp = self._llm.temperature if temperature is None else temperature
        tokens = self._llm.max_tokens if max_tokens is None else max_tokens

        last_error: BaseException | None = None
        session_retries = 0

        for slug in chain:
            for attempt in range(1, self._llm.retries.max_attempts + 1):
                started = time.perf_counter()
                try:
                    result = self._call_once(
                        slug=slug,
                        messages=messages,
                        temperature=temp,
                        max_tokens=tokens,
                        retries=session_retries,
                        started=started,
                        role=role,
                    )
                    return result
                except BaseException as exc:
                    exc = classify_api_error(exc)
                    last_error = exc
                    latency_ms = (time.perf_counter() - started) * 1000
                    self._usage.add(
                        LLMUsage(
                            model_slug=slug,
                            input_tokens=0,
                            output_tokens=0,
                            latency_ms=latency_ms,
                            retries=session_retries,
                            success=False,
                            role=role,
                        )
                    )
                    if is_model_unavailable(exc):
                        logger.warning(
                            "Model %s unavailable, trying fallback: %s",
                            slug,
                            exc,
                        )
                        break
                    if is_retryable(exc) and attempt < self._llm.retries.max_attempts:
                        session_retries += 1
                        delay = compute_backoff_s(attempt, self._llm.retries)
                        logger.warning(
                            "LLM call failed (attempt %s/%s, model=%s): %s; retry in %.1fs",
                            attempt,
                            self._llm.retries.max_attempts,
                            slug,
                            exc,
                            delay,
                        )
                        time.sleep(delay)
                        continue
                    logger.warning(
                        "LLM call failed for model %s after %s attempt(s): %s",
                        slug,
                        attempt,
                        exc,
                    )
                    break

        models = ", ".join(chain)
        raise LLMRetryExhaustedError(
            f"All models exhausted ({models}): {last_error}"
        ) from last_error

    def _model_chain_for_role(self, role: LLMRole) -> list[str]:
        choice: ModelChoice = getattr(self._llm.models, role)
        return choice.chain

    def _call_once(
        self,
        *,
        slug: str,
        messages: list[ChatCompletionMessageParam],
        temperature: float,
        max_tokens: int,
        retries: int,
        started: float,
        role: LLMRole | None,
    ) -> ChatResult:
        uri = self.model_uri(slug)
        completion = self._client.chat.completions.create(
            model=uri,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        choice = completion.choices[0]
        raw_content = choice.message.content
        content = raw_content or ""
        if not content.strip():
            _log_empty_completion_diagnostics(
                slug=slug,
                uri=uri,
                role=role,
                messages=messages,
                choice=choice,
                completion=completion,
                usage_obj=completion.usage,
            )
        usage_obj = completion.usage
        input_tokens = int(usage_obj.prompt_tokens or 0) if usage_obj else 0
        output_tokens = int(usage_obj.completion_tokens or 0) if usage_obj else 0
        usage = LLMUsage(
            model_slug=slug,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            retries=retries,
            success=True,
            role=role,
        )
        self._usage.add(usage)
        return ChatResult(
            content=content,
            model_slug=slug,
            model_uri=uri,
            usage=usage,
        )


class ElizaLLMClient(YandexLLMClient):
    """OpenAI-compatible client for internal Eliza.

    Differences vs Yandex Cloud:
    - `model_uri(model_slug)` returns the raw model id (no `gpt://...` prefix)
    - Uses `Authorization: OAuth <token>` header (token from env via `Secrets`)
    """

    def __init__(
        self,
        *,
        base_url: str,
        oauth_token: str,
        llm: LLMConfig,
        client: OpenAI | None = None,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        if not base_url or not oauth_token:
            raise LLMConfigError("base_url and oauth_token are required")
        # OpenAI client: avoid leaking token; never log it.
        # The official SDK uses Bearer by default; Eliza expects OAuth.
        if client is None:
            try:
                client = OpenAI(
                    api_key="unused",
                    base_url=base_url,
                    timeout=float(llm.timeout_s),
                    default_headers={"Authorization": f"OAuth {oauth_token}"},
                )
            except TypeError:
                # Older SDK versions may not support default_headers. Fall back to api_key.
                # Some Eliza deployments accept Bearer; if not, this will error clearly.
                client = OpenAI(
                    api_key=oauth_token,
                    base_url=base_url,
                    timeout=float(llm.timeout_s),
                )
        # Reuse parent retry/usage logic; folder id is not used for Eliza.
        super().__init__(
            folder_id="eliza",
            api_key="unused",
            llm=llm,
            client=client,
            usage_tracker=usage_tracker,
        )
        self._base_url = base_url

    @classmethod
    def from_config(cls, config: Config) -> ElizaLLMClient:
        base_url, token = config.secrets.require_eliza()
        return cls(base_url=base_url, oauth_token=token, llm=config.llm)

    def model_uri(self, model_slug: str) -> str:
        return model_slug


def create_llm_client(config: Config) -> YandexLLMClient:
    """Factory for model provider selection.

    Controlled via env `YDBDOC_MODEL_PROVIDER`:
    - `yandex_cloud` (default): Yandex AI Studio via existing secrets
    - `eliza`: Eliza OpenAI-compatible transport
    """
    provider = (os.environ.get("YDBDOC_MODEL_PROVIDER") or "yandex_cloud").strip()
    if provider == "yandex_cloud":
        return YandexLLMClient.from_config(config)
    if provider == "eliza":
        return ElizaLLMClient.from_config(config)
    raise LLMConfigError(
        "Unknown YDBDOC_MODEL_PROVIDER. Expected 'yandex_cloud' or 'eliza'."
    )
