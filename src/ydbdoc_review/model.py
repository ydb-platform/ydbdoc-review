"""One paid request, optionally one technical fallback; no quality/retry loops.

The runner supplies persistent callbacks (YDB adapter) and admits the daily budget
before constructing/calling this client. Callback errors propagate, never fallback.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

import requests
from requests.adapters import HTTPAdapter

from ydbdoc_review.llm.tls import eliza_tls_verify, public_ca_bundle

Operation = Literal["translation", "critic", "repair"]
OPERATIONS = ("translation", "critic", "repair")


@dataclass(frozen=True)
class Endpoint:
    """A single concrete provider/model, with existing provider auth conventions."""

    provider: Literal["eliza", "yandex_cloud"]
    base_url: str
    model: str
    token: str = field(repr=False)
    folder_id: str | None = None
    # Opt-in only: the operator must verify support for this exact deployment.
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        if self.provider not in ("eliza", "yandex_cloud"):
            raise ValueError("Unsupported model provider")
        if not self.base_url or not self.model or not self.token:
            raise ValueError("Model endpoint, model and token are required")
        if self.provider == "yandex_cloud" and not self.folder_id:
            raise ValueError("Yandex Cloud folder_id is required")

        if self.reasoning_effort is not None and (
            self.provider != "yandex_cloud"
            or self.reasoning_effort not in ("none", "minimal", "low", "medium", "high", "xhigh")
        ):
            raise ValueError("Unsupported reasoning_effort configuration")

    @property
    def url(self) -> str:
        root = self.base_url.rstrip("/")
        if self.provider == "eliza":
            return f"{root}/raw/internal/{self.model}/v1/chat/completions"
        return f"{root}/chat/completions"


@dataclass(frozen=True)
class ModelChoice:
    main: Endpoint
    alternative: Endpoint | None = None

    def __post_init__(self) -> None:
        if self.alternative is not None and (
            self.main.provider,
            self.main.url,
            self.main.model,
            self.main.folder_id,
        ) == (
            self.alternative.provider,
            self.alternative.url,
            self.alternative.model,
            self.alternative.folder_id,
        ):
            raise ValueError("Alternative must be a different model or provider endpoint")


@dataclass(frozen=True)
class RequestRecord:
    id: str
    operation: Operation
    provider: str
    model: str
    url: str
    payload: dict[str, Any]
    created_at: datetime
    # Index within this logical call: 0 = main, 1 = alternative.
    attempt: int


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_rub: Decimal | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class AttemptRecord:
    request: RequestRecord
    response_text: str | None
    status_code: int | None
    error: str | None
    usage: Usage


@dataclass(frozen=True)
class ModelResult:
    content: str
    attempt: AttemptRecord
    finish_reason: str | None

    @property
    def response_status(self) -> Literal["complete", "length"]:
        # A truncated text remains available to the document/repair layer.
        return "length" if self.finish_reason == "length" else "complete"


class ModelError(Exception):
    """Failed transport or unusable response; callers must mark work unfinished."""

    def __init__(
        self, message: str, *, fallback_allowed: bool = False,
        kind: Literal["transport", "empty", "length", "invalid_format"] = "transport",
    ) -> None:
        super().__init__(message)
        self.fallback_allowed = fallback_allowed
        self.kind = kind


def _token_count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _completion_error(data: Any, output_limit: int) -> ModelError | None:
    """Validate the envelope, never interpret reasoning_content as translation."""
    try:
        choices = data["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError
        first = choices[0]
        message = first["message"]
        if not isinstance(message, dict) or not ({"content", "reasoning_content"} & message.keys()):
            raise ValueError
        finish = first.get("finish_reason")
        if finish not in (None, "stop", "length"):
            raise ValueError
        content = message.get("content")
        if finish == "length":
            usage = data.get("usage") or {}
            details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
            reasoning = _token_count(details.get("reasoning_tokens")) if isinstance(details, dict) else None
            detail = f"; reasoning_tokens={reasoning}" if reasoning is not None else ""
            if message.get("reasoning_content"):
                detail += "; reasoning content present (not a translation)"
            return ModelError(
                f"Model output length limit reached (max_tokens={output_limit}{detail})",
                kind="length",
            )
        if content is None or (isinstance(content, str) and not content.strip()):
            return ModelError("Model returned empty completion content", kind="empty")
        if not isinstance(content, str):
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError):
        return ModelError("Model returned invalid response JSON/format", kind="invalid_format")
    return None


def _http_error(status: int, data: Any) -> ModelError:
    # A generic 404 (wrong URL), auth failure or rate limit is not evidence that
    # the configured model is unavailable. Only structured provider codes qualify.
    error = data.get("error", {}) if isinstance(data, dict) else {}
    code = error.get("code") if isinstance(error, dict) else None
    unavailable = status in (404, 410) and code in (
        "model_not_found",
        "model_unavailable",
        "provider_unavailable",
    )
    return ModelError(
        f"Model HTTP {status}",
        fallback_allowed=status == 408 or 500 <= status <= 599 or unavailable,
    )


class ModelClient:
    """Synchronous HTTP transport shared by translation, critic and repair.

    `record_request` must persist the entire request before returning. After every
    attempt `record_attempt` receives raw response/error plus available usage.
    `attempts` retains paid work even if persistence fails. Credentials are never
    included in these records. `cost_resolver`, if supplied, must use an agreed
    billing source; token estimates and unknown usage are never treated as zero.
    """

    def __init__(
        self,
        *,
        record_request: Callable[[RequestRecord], None],
        record_attempt: Callable[[AttemptRecord], None],
        timeout_s: float = 120,
        cost_resolver: Callable[[Endpoint, dict[str, Any]], Decimal | None] | None = None,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.record_request = record_request
        self.record_attempt = record_attempt
        self.timeout_s = timeout_s
        self.cost_resolver = cost_resolver
        self.attempts: list[AttemptRecord] = []
        self._http = requests.Session()
        self._http.trust_env = False
        # No SDK, HTTP retries, redirects or hidden replays of a paid request.
        self._http.mount("https://", HTTPAdapter(max_retries=0))
        self._http.mount("http://", HTTPAdapter(max_retries=0))

    def close(self) -> None:
        self._http.close()

    def cost_breakdown(self) -> dict[str, Decimal | None]:
        def total(records: list[AttemptRecord]) -> Decimal | None:
            costs = [record.usage.cost_rub for record in records]
            return None if any(cost is None for cost in costs) else sum(costs, Decimal(0))

        result = {
            operation: total([r for r in self.attempts if r.request.operation == operation])
            for operation in OPERATIONS
        }
        result["total"] = total(self.attempts)
        return result

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        operation: Operation,
        choice: ModelChoice,
        max_tokens: int,
        temperature: float = 0,
    ) -> ModelResult:
        if operation not in OPERATIONS:
            raise ValueError(f"Unknown operation: {operation}")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        endpoints = [choice.main]
        if choice.alternative is not None:
            endpoints.append(choice.alternative)
        for index, endpoint in enumerate(endpoints):
            payload = {
                "messages": deepcopy(messages),
                "max_tokens": max_tokens,
                "temperature": temperature,
                "thinking": {"type": "disabled"},
            }
            if endpoint.reasoning_effort is not None:
                payload["reasoning_effort"] = endpoint.reasoning_effort
            if endpoint.provider == "yandex_cloud":
                payload["model"] = f"gpt://{endpoint.folder_id}/{endpoint.model}"
            request = RequestRecord(
                uuid4().hex,
                operation,
                endpoint.provider,
                endpoint.model,
                endpoint.url,
                payload,
                datetime.now(UTC),
                index,
            )
            # Outside transport error handling: failed storage must stop the call.
            self.record_request(deepcopy(request))
            result, error = self._attempt(endpoint, request)
            self.attempts.append(result)
            self.record_attempt(deepcopy(result))
            if error is None:
                data = json.loads(result.response_text)
                first = data["choices"][0]
                return ModelResult(first["message"]["content"], result, first.get("finish_reason"))
            if not error.fallback_allowed or index == len(endpoints) - 1:
                raise error
        raise AssertionError("unreachable")

    def _attempt(
        self,
        endpoint: Endpoint,
        request: RequestRecord,
    ) -> tuple[AttemptRecord, ModelError | None]:
        response_text = None
        status = None
        data: Any = None
        error = None
        try:
            response = self._http.post(
                endpoint.url,
                headers={
                    "Authorization": f"{'OAuth' if endpoint.provider == 'eliza' else 'Bearer'} {endpoint.token}",
                    "Content-Type": "application/json",
                },
                json=request.payload,
                timeout=self.timeout_s,
                verify=eliza_tls_verify() if endpoint.provider == "eliza" else public_ca_bundle(),
                allow_redirects=False,
            )
            status = response.status_code
            response_text = response.text
            try:
                data = response.json()
            except ValueError:
                pass
            if not 200 <= status < 300:
                error = _http_error(status, data)
            else:
                error = _completion_error(data, request.payload["max_tokens"])
        except requests.exceptions.SSLError:
            error = ModelError("Model TLS verification failed")
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ):
            error = ModelError("Model timeout/network error", fallback_allowed=True)
        except requests.exceptions.RequestException:
            error = ModelError("Model HTTP transport error")
        raw_usage = data.get("usage") if isinstance(data, dict) else None
        raw_usage = raw_usage if isinstance(raw_usage, dict) else None
        usage = Usage(
            _token_count(raw_usage.get("prompt_tokens")) if raw_usage else None,
            _token_count(raw_usage.get("completion_tokens")) if raw_usage else None,
            raw=deepcopy(raw_usage),
        )
        record = AttemptRecord(request, response_text, status, str(error) if error else None, usage)
        # Billing resolver failures also retain paid work and stop without fallback.
        if self.cost_resolver is not None and isinstance(data, dict):
            try:
                cost = self.cost_resolver(endpoint, data)
                if cost is not None and (
                    not isinstance(cost, Decimal) or not cost.is_finite() or cost < 0
                ):
                    raise ValueError("Billing cost must be a nonnegative finite Decimal or None")
            except Exception:
                self.attempts.append(record)
                self.record_attempt(deepcopy(record))
                raise
            record = AttemptRecord(
                request,
                response_text,
                status,
                record.error,
                Usage(usage.input_tokens, usage.output_tokens, cost, usage.raw),
            )
        # Preserve partial text for publication/repair; callers already inspect
        # finish_reason. The attempt still records the length diagnosis and cost.
        if error is not None and error.kind == "length":
            content = data["choices"][0]["message"].get("content")
            if isinstance(content, str) and content.strip():
                return record, None
        return record, error
