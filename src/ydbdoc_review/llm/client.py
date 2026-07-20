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
import requests
from openai.types.chat import ChatCompletionMessageParam

from ydbdoc_review.config.loader import Config, LLMConfig
from ydbdoc_review.llm.errors import (
    LLMConfigError,
    LLMRequestError,
    LLMRetryableRequestError,
    LLMRetryExhaustedError,
)
from ydbdoc_review.llm.tls import ELIZA_CA_BUNDLE_ENV
from ydbdoc_review.llm.retry import (
    HTTP_RATE_LIMIT,
    classify_api_error,
    compute_backoff_s,
    is_eliza_model_overloaded,
    is_model_unavailable,
    is_requests_ssl_error,
    is_retryable,
    parse_retry_after_s,
    retry_delay_s,
    should_advance_eliza_model_chain,
)
from ydbdoc_review.llm.role_chains import ensure_disjoint_translate_critic_chains
from ydbdoc_review.shutdown import interruptible_sleep
from ydbdoc_review.llm.usage import LLMUsage, UsageTracker

logger = logging.getLogger(__name__)

LLMRole = Literal["analyze", "translate", "critic"]

from ydbdoc_review.llm.tls import eliza_tls_verify


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
    def from_config(
        cls,
        config: Config,
        *,
        usage_tracker: UsageTracker | None = None,
    ) -> YandexLLMClient:
        folder_id, api_key = config.secrets.require_yandex()
        return cls(
            folder_id=folder_id,
            api_key=api_key,
            llm=config.llm,
            usage_tracker=usage_tracker,
        )

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
                        interruptible_sleep(delay)
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
        if role == "analyze":
            return list(self._llm.models.analyze.chain)
        translate, critic = ensure_disjoint_translate_critic_chains(
            self._llm.models.translate.chain,
            self._llm.models.critic.chain,
        )
        if role == "translate":
            return translate
        if role == "critic":
            return critic
        raise LLMConfigError(f"Unknown LLM role: {role!r}")

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
    """Internal Eliza transport (``requests.Session``, OAuth header).

    Shares the ``chat()`` / ``usage_tracker`` surface with ``YandexLLMClient`` but
    does **not** call ``super().__init__()`` — no OpenAI SDK client. Unsupported
    roles (e.g. ``analyze``) fail fast instead of falling back to Yandex model slugs.
    """

    def __init__(
        self,
        *,
        api_root: str,
        oauth_token: str,
        llm: LLMConfig,
        usage_tracker: UsageTracker | None = None,
        translate_default: str = "deepseek-v4-flash",
        critic_default: str = "gpt-oss-120b",
    ) -> None:
        if not api_root or not oauth_token:
            raise LLMConfigError("api_root and oauth_token are required")
        self._api_root = api_root.rstrip("/")
        self._oauth_token = oauth_token
        self._llm = llm
        self._usage = usage_tracker or UsageTracker()
        self._translate_default = translate_default
        self._critic_default = critic_default
        self._http = requests.Session()
        self._http.verify = eliza_tls_verify()

    @classmethod
    def from_config(
        cls,
        config: Config,
        *,
        usage_tracker: UsageTracker | None = None,
    ) -> ElizaLLMClient:
        root, token = config.secrets.require_eliza_api_root()
        return cls(
            api_root=root,
            oauth_token=token,
            llm=config.llm,
            usage_tracker=usage_tracker,
        )

    def model_uri(self, model_slug: str) -> str:
        return model_slug

    def _internal_base_url(self, model_id: str) -> str:
        root = self._api_root
        model = model_id.strip()
        if not model:
            raise LLMConfigError("Empty Eliza model id")
        # {ELIZA_API_ROOT}/raw/internal/{model_id}/v1/chat/completions
        return f"{root}/raw/internal/{model}/v1"

    @staticmethod
    def _parse_eliza_completion_content(data: object) -> str:
        """Extract assistant text from Eliza chat completion JSON."""
        if not isinstance(data, dict):
            raise LLMRetryableRequestError(
                "Eliza HTTP 200: response is not a JSON object"
            )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMRetryableRequestError("Eliza HTTP 200: empty choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise LLMRetryableRequestError(
                "Eliza HTTP 200: invalid choice entry"
            )
        message = first.get("message")
        if not isinstance(message, dict):
            raise LLMRetryableRequestError(
                "Eliza HTTP 200: missing message in choice"
            )
        if "content" not in message:
            raise LLMRetryableRequestError(
                "Eliza HTTP 200: missing content in message"
            )
        content = str(message.get("content") or "")
        if not content.strip():
            raise LLMRetryableRequestError(
                "Eliza HTTP 200: empty content in message"
            )
        return content

    def _model_chain_for_role(self, role: LLMRole) -> list[str]:
        if role == "analyze":
            raise LLMConfigError(
                f'role "analyze" has no internal Eliza model '
                "(doc_translate uses deterministic planning — §6.30); "
                "pass model= explicitly or use yandex_cloud provider"
            )
        if role not in ("translate", "critic"):
            raise LLMConfigError(
                f'role "{role}" has no internal Eliza model; '
                "pass model= explicitly or use yandex_cloud provider"
            )
        translate, critic = ensure_disjoint_translate_critic_chains(
            self._eliza_raw_model_chain("translate"),
            self._eliza_raw_model_chain("critic"),
        )
        return translate if role == "translate" else critic

    def _eliza_raw_model_chain(self, role: Literal["translate", "critic"]) -> list[str]:
        env_primary = {
            "translate": "YDBDOC_MODEL_TRANSLATE",
            "critic": "YDBDOC_MODEL_CHECK",
        }[role]
        default_primary = {
            "translate": self._translate_default,
            "critic": self._critic_default,
        }[role]

        primary = (os.environ.get(env_primary) or "").strip()
        if not primary:
            eliza_cfg = self._llm.eliza
            if eliza_cfg is not None:
                choice = getattr(eliza_cfg, role)
                primary = choice.primary
            else:
                primary = default_primary
            if primary == "deepseek-v32":
                primary = default_primary

        fallbacks = self._eliza_env_fallbacks(role)
        if not fallbacks and self._llm.eliza is not None:
            fallbacks = list(getattr(self._llm.eliza, role).fallbacks)

        return self._dedupe_model_chain([primary, *fallbacks])

    def _eliza_model_chain(self, role: Literal["translate", "critic"]) -> list[str]:
        """Backward-compat alias — prefer ``model_chain_for_role``."""
        return self._model_chain_for_role(role)

    @staticmethod
    def _dedupe_model_chain(models: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for model in models:
            slug = model.strip()
            if slug and slug not in seen:
                seen.add(slug)
                out.append(slug)
        return out

    @staticmethod
    def _eliza_env_fallbacks(role: LLMRole) -> list[str]:
        """Optional Eliza fallbacks from env (Nirvana / local overrides)."""
        keys: tuple[str, ...]
        if role == "translate":
            keys = ("YDBDOC_ELIZA_TRANSLATE_FALLBACKS",)
        elif role == "critic":
            keys = (
                "YDBDOC_ELIZA_CHECK_FALLBACKS",
                "YDBDOC_ELIZA_CRITIC_FALLBACKS",
            )
        else:
            return []
        for key in keys:
            raw = (os.environ.get(key) or "").strip()
            if raw:
                return [part.strip() for part in raw.split(",") if part.strip()]
        return []

    @staticmethod
    def _eliza_http_error(
        resp: requests.Response,
        *,
        oauth_token: str,
    ) -> LLMRetryableRequestError | LLMRequestError:
        from ydbdoc_review.llm.retry import build_eliza_http_error

        err = build_eliza_http_error(
            resp.status_code,
            resp.text,
            redact=oauth_token,
        )
        if isinstance(err, LLMRetryableRequestError) and err.status_code is not None:
            return LLMRetryableRequestError(
                str(err),
                status_code=err.status_code,
                retry_after_s=parse_retry_after_s(resp.headers.get("Retry-After")),
            )
        return err

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
        """Guard: Eliza never uses the OpenAI SDK path from ``YandexLLMClient``."""
        raise LLMConfigError(
            "ElizaLLMClient must use chat() over requests.Session, not _call_once()"
        )

    def chat(
        self,
        messages: list[ChatCompletionMessageParam],
        *,
        role: LLMRole | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """Call internal Eliza chat completions with retries.

        IMPORTANT: model id is encoded in the URL path; request body MUST NOT
        include `model`.
        """
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

        for model_idx, slug in enumerate(chain):
            url = f"{self._internal_base_url(slug)}/chat/completions"
            headers = {
                "authorization": f"OAuth {self._oauth_token}",
                "content-type": "application/json",
            }
            payload = {
                "messages": messages,
                "temperature": temp,
                "max_tokens": tokens,
            }
            general_failures = 0
            rate_limit_failures = 0
            while True:
                started = time.perf_counter()
                try:
                    logger.debug(
                        "Eliza request: role=%s model=%s url=%s auth=OAuth",
                        role,
                        slug,
                        url,
                    )
                    resp = self._http.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=float(self._llm.timeout_s),
                    )
                    latency_ms = (time.perf_counter() - started) * 1000

                    if resp.status_code >= 400:
                        http_err = self._eliza_http_error(
                            resp, oauth_token=self._oauth_token
                        )
                        raise http_err

                    data = resp.json()
                    content = self._parse_eliza_completion_content(data)
                    usage_obj = data.get("usage") or {}
                    input_tokens = int(usage_obj.get("prompt_tokens") or 0)
                    output_tokens = int(usage_obj.get("completion_tokens") or 0)
                    usage = LLMUsage(
                        model_slug=slug,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=latency_ms,
                        retries=session_retries,
                        success=True,
                        role=role,
                    )
                    self._usage.add(usage)
                    return ChatResult(
                        content=content,
                        model_slug=slug,
                        model_uri=url,
                        usage=usage,
                    )
                except LLMRequestError as exc:
                    if not isinstance(exc, LLMRetryableRequestError):
                        raise
                    last_error = exc
                except requests.exceptions.SSLError as exc:
                    raise LLMRequestError(
                        "Eliza TLS verification failed "
                        f"(set {ELIZA_CA_BUNDLE_ENV}): {exc}"
                    ) from exc
                except requests.exceptions.Timeout as exc:
                    last_error = exc
                except requests.exceptions.ConnectionError as exc:
                    if is_requests_ssl_error(exc):
                        raise LLMRequestError(
                            "Eliza TLS verification failed "
                            f"(set {ELIZA_CA_BUNDLE_ENV}): {exc}"
                        ) from exc
                    last_error = exc
                except ValueError as exc:
                    raise LLMRequestError(
                        f"Eliza response is not valid JSON: {exc}"
                    ) from exc

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

                is_rate_limit = (
                    isinstance(last_error, LLMRetryableRequestError)
                    and last_error.status_code == HTTP_RATE_LIMIT
                )
                if is_rate_limit:
                    rate_limit_failures += 1
                    max_tries = self._llm.retries.rate_limit.max_attempts
                    if is_eliza_model_overloaded(last_error):
                        max_tries = 1
                    failure_no = rate_limit_failures
                    retry_after_s = (
                        last_error.retry_after_s
                        if isinstance(last_error, LLMRetryableRequestError)
                        else None
                    )
                else:
                    general_failures += 1
                    max_tries = self._llm.retries.max_attempts
                    failure_no = general_failures
                    retry_after_s = None
                    status_code = (
                        last_error.status_code
                        if isinstance(last_error, LLMRetryableRequestError)
                        else None
                    )

                if failure_no >= max_tries:
                    if (
                        model_idx + 1 < len(chain)
                        and should_advance_eliza_model_chain(last_error)
                    ):
                        logger.warning(
                            "Eliza model %s exhausted retries, trying next model in chain: %s",
                            slug,
                            last_error,
                        )
                        break
                    models = ", ".join(chain)
                    if is_rate_limit and len(chain) == 1:
                        raise LLMRetryExhaustedError(
                            f"Eliza rate-limit (429) retries exhausted ({models}): {last_error}"
                        ) from last_error
                    raise LLMRetryExhaustedError(
                        f"All models exhausted ({models}): {last_error}"
                    ) from last_error

                session_retries += 1
                if is_rate_limit:
                    delay = retry_delay_s(
                        attempt=failure_no,
                        retries=self._llm.retries,
                        status_code=HTTP_RATE_LIMIT,
                        retry_after_s=retry_after_s,
                    )
                    retry_label = "rate-limit retry"
                else:
                    delay = retry_delay_s(
                        attempt=failure_no,
                        retries=self._llm.retries,
                        status_code=status_code,
                    )
                    retry_label = "retry"
                logger.warning(
                    "Eliza call failed (%s %s/%s, model=%s): %s; sleep %.1fs",
                    retry_label,
                    failure_no,
                    max_tries,
                    slug,
                    last_error,
                    delay,
                )
                interruptible_sleep(delay)

        models = ", ".join(chain)
        raise LLMRetryExhaustedError(
            f"All models exhausted ({models}): {last_error}"
        ) from last_error


def create_llm_client(
    config: Config,
    *,
    usage_tracker: UsageTracker | None = None,
) -> YandexLLMClient:
    """Factory for model provider selection.

    ``YDBDOC_MODEL_PROVIDER`` env overrides ``config.llm.provider`` when set.
    """
    explicit = (os.environ.get("YDBDOC_MODEL_PROVIDER") or "").strip()
    provider = explicit or (config.llm.provider or "yandex_cloud").strip()
    if provider in ("yandex_cloud", "yandex"):
        return YandexLLMClient.from_config(config, usage_tracker=usage_tracker)
    if provider == "eliza":
        return ElizaLLMClient.from_config(config, usage_tracker=usage_tracker)
    raise LLMConfigError(
        "Unknown YDBDOC_MODEL_PROVIDER. Expected 'yandex_cloud' or 'eliza'."
    )
