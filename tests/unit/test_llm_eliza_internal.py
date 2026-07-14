from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.client import ElizaLLMClient, create_llm_client
from ydbdoc_review.llm.errors import (
    LLMConfigError,
    LLMRequestError,
    LLMRetryableRequestError,
    LLMRetryExhaustedError,
)
from ydbdoc_review.llm.usage import UsageTracker


def _resp(
    status: int,
    payload: dict,
    *,
    headers: dict[str, str] | None = None,
) -> SimpleNamespace:
    hdrs = headers or {}
    return SimpleNamespace(
        status_code=status,
        text=str(payload),
        headers=hdrs,
        json=lambda: payload,
    )


def _client(**kwargs) -> ElizaLLMClient:
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    return ElizaLLMClient(
        api_root="https://api.eliza.yandex.net",
        oauth_token="t",
        llm=cfg.llm,
        **kwargs,
    )


def test_eliza_internal_builds_url_with_model_in_path_and_oauth_header():
    client = _client()

    with patch.object(client._http, "post") as post:
        post.return_value = _resp(
            200,
            {"choices": [{"message": {"content": "Hello!"}}]},
        )
        out = client.chat(
            [{"role": "user", "content": "Translate: Привет!"}],
            role="translate",
            model="deepseek-v4-flash",
        )

    assert out.content.strip() == "Hello!"
    args, kwargs = post.call_args
    assert args[0].startswith(
        "https://api.eliza.yandex.net/raw/internal/deepseek-v4-flash/v1/chat/completions"
    )
    hdrs = {k.lower(): v for k, v in (kwargs.get("headers") or {}).items()}
    assert "authorization" in hdrs
    assert hdrs["authorization"].startswith("OAuth ")
    assert "bearer" not in hdrs["authorization"].lower()
    assert "model" not in (kwargs.get("json") or {})


def test_eliza_internal_retries_on_503():
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    cfg.llm.retries.max_attempts = 2
    cfg.llm.retries.backoff_initial_s = 0.0
    cfg.llm.retries.backoff_factor = 1.0

    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("time.sleep") as sleep,
    ):
        post.side_effect = [
            _resp(503, {"error": "Service unavailable"}),
            _resp(200, {"choices": [{"message": {"content": "ok"}}]}),
        ]
        out = client.chat(
            [{"role": "user", "content": "x"}],
            role="translate",
            model="deepseek-v4-flash",
        )

    assert out.content == "ok"
    assert post.call_count == 2


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_eliza_non_retryable_4xx_fail_fast(status_code: int):
    token = "secret-oauth-token-xyz"
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": token})
    cfg.llm.retries.max_attempts = 3
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token=token, llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("time.sleep") as sleep,
    ):
        post.return_value = _resp(
            status_code,
            {"error": f"OAuth {token} rejected"},
        )
        with pytest.raises(LLMRequestError, match=str(status_code)) as exc_info:
            client.chat(
                [{"role": "user", "content": "x"}],
                role="translate",
                model="deepseek-v4-flash",
            )

    assert post.call_count == 1
    sleep.assert_not_called()
    assert token not in str(exc_info.value)


def test_eliza_internal_401_fails_fast_without_retry():
    token = "secret-oauth-token-xyz"
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": token})
    cfg.llm.retries.max_attempts = 3
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token=token, llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("time.sleep") as sleep,
    ):
        post.return_value = _resp(401, {"error": "Unauthorized"})
        with pytest.raises(LLMRequestError, match="401") as exc_info:
            client.chat(
                [{"role": "user", "content": "x"}],
                role="translate",
                model="deepseek-v4-flash",
            )

    assert post.call_count == 1
    sleep.assert_not_called()
    assert token not in str(exc_info.value)


def test_eliza_internal_ssl_error_fails_fast_without_retry():
    import requests

    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    cfg.llm.retries.max_attempts = 3
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("time.sleep") as sleep,
    ):
        post.side_effect = requests.exceptions.SSLError(
            "certificate verify failed: self-signed certificate in certificate chain"
        )
        with pytest.raises(LLMRequestError, match="TLS verification failed") as exc_info:
            client.chat(
                [{"role": "user", "content": "x"}],
                role="translate",
                model="deepseek-v4-flash",
            )

    assert post.call_count == 1
    sleep.assert_not_called()
    assert "YDBDOC_ELIZA_CA_BUNDLE" in str(exc_info.value)


def test_eliza_connection_error_wrapping_ssl_fails_fast_without_retry():
    import requests

    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    cfg.llm.retries.max_attempts = 3
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )
    ssl_exc = requests.exceptions.SSLError(
        "certificate verify failed: self-signed certificate in certificate chain"
    )
    conn_exc = requests.exceptions.ConnectionError(ssl_exc)

    with (
        patch.object(client._http, "post") as post,
        patch("time.sleep") as sleep,
    ):
        post.side_effect = conn_exc
        with pytest.raises(LLMRequestError, match="TLS verification failed"):
            client.chat(
                [{"role": "user", "content": "x"}],
                role="translate",
                model="deepseek-v4-flash",
            )

    assert post.call_count == 1
    sleep.assert_not_called()


def test_eliza_429_honors_retry_after_header():
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    cfg.llm.retries.rate_limit.max_attempts = 3
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("time.sleep") as sleep,
    ):
        post.side_effect = [
            _resp(429, {"error": "overloaded"}, headers={"Retry-After": "5"}),
            _resp(200, {"choices": [{"message": {"content": "ok"}}]}),
        ]
        out = client.chat(
            [{"role": "user", "content": "x"}],
            role="translate",
            model="deepseek-v4-flash",
        )

    assert out.content == "ok"
    assert post.call_count == 2
    sleep.assert_called_once_with(5.0)


def test_eliza_429_rate_limit_retries_exhausted_with_clear_error():
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    cfg.llm.retries.rate_limit.max_attempts = 4
    cfg.llm.retries.rate_limit.backoff_initial_s = 0.0
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("time.sleep"),
    ):
        post.return_value = _resp(
            429, {"error": "overloaded"}, headers={"Retry-After": "1"}
        )
        with pytest.raises(LLMRetryExhaustedError, match="rate-limit \\(429\\)"):
            client.chat(
                [{"role": "user", "content": "x"}],
                role="translate",
                model="deepseek-v4-flash",
            )

    assert post.call_count == 4


def test_eliza_translate_chain_ignores_yaml_fallbacks(monkeypatch):
    monkeypatch.delenv("YDBDOC_MODEL_TRANSLATE", raising=False)
    monkeypatch.delenv("YDBDOC_ELIZA_TRANSLATE_FALLBACKS", raising=False)
    client = _client()
    assert client.model_chain_for_role("translate") == ["deepseek-v4-flash"]


def test_eliza_internal_503_retries_until_exhausted():
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    cfg.llm.retries.max_attempts = 3
    cfg.llm.retries.backoff_initial_s = 0.0
    cfg.llm.retries.backoff_factor = 1.0
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("time.sleep"),
    ):
        post.return_value = _resp(503, {"error": "Service unavailable"})
        with pytest.raises(LLMRetryExhaustedError):
            client.chat(
                [{"role": "user", "content": "x"}],
                role="translate",
                model="deepseek-v4-flash",
            )

    assert post.call_count == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        {},
        {"choices": None},
    ],
)
def test_parse_eliza_completion_content_empty_choices(payload):
    with pytest.raises(LLMRetryableRequestError, match="empty choices"):
        ElizaLLMClient._parse_eliza_completion_content(payload)


@pytest.mark.parametrize(
    "payload,match",
    [
        ({"choices": [{}]}, "missing message"),
        ({"choices": [{"finish_reason": "stop"}]}, "missing message"),
        ({"choices": [{"message": {}}]}, "missing content"),
        ({"choices": [{"message": {"content": ""}}]}, "empty content"),
        ({"choices": [{"message": {"content": "   "}}]}, "empty content"),
    ],
)
def test_parse_eliza_completion_content_malformed_choice_retries(payload, match):
    with pytest.raises(LLMRetryableRequestError, match=match):
        ElizaLLMClient._parse_eliza_completion_content(payload)


@pytest.mark.parametrize("response_body", [{"choices": []}, {}])
def test_eliza_internal_empty_choices_retries_without_index_error(response_body):
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    cfg.llm.retries.max_attempts = 2
    cfg.llm.retries.backoff_initial_s = 0.0
    cfg.llm.retries.backoff_factor = 1.0
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("time.sleep"),
    ):
        post.side_effect = [
            _resp(200, response_body),
            _resp(200, {"choices": [{"message": {"content": "recovered"}}]}),
        ]
        out = client.chat(
            [{"role": "user", "content": "x"}],
            role="translate",
            model="deepseek-v4-flash",
        )

    assert out.content == "recovered"
    assert post.call_count == 2


def test_eliza_internal_missing_message_retries():
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    cfg.llm.retries.max_attempts = 2
    cfg.llm.retries.backoff_initial_s = 0.0
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("time.sleep"),
    ):
        post.side_effect = [
            _resp(200, {"choices": [{"finish_reason": "stop"}]}),
            _resp(200, {"choices": [{"message": {"content": "ok"}}]}),
        ]
        out = client.chat(
            [{"role": "user", "content": "x"}],
            role="translate",
            model="deepseek-v4-flash",
        )

    assert out.content == "ok"
    assert post.call_count == 2


def test_eliza_session_never_disables_tls_verification():
    client = _client()
    assert client._http.verify is not False


def test_eliza_session_uses_ydbdoc_ca_bundle(tmp_path, monkeypatch):
    ca_path = tmp_path / "internal-ca.pem"
    ca_path.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    monkeypatch.setenv("YDBDOC_ELIZA_CA_BUNDLE", str(ca_path))

    client = _client()
    assert client._http.verify == str(ca_path)

    with patch.object(client._http, "post") as post:
        post.return_value = _resp(
            200,
            {"choices": [{"message": {"content": "ok"}}]},
        )
        client.chat(
            [{"role": "user", "content": "x"}],
            role="translate",
            model="deepseek-v4-flash",
        )

    assert client._http.verify == str(ca_path)
    assert post.call_args.kwargs.get("verify", client._http.verify) is not False


def test_eliza_session_ydbdoc_ca_overrides_requests_env(tmp_path, monkeypatch):
    eliza_ca = tmp_path / "eliza-ca.pem"
    eliza_ca.write_text("eliza", encoding="utf-8")
    other_ca = tmp_path / "other-ca.pem"
    other_ca.write_text("other", encoding="utf-8")
    monkeypatch.setenv("YDBDOC_ELIZA_CA_BUNDLE", str(eliza_ca))
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(other_ca))

    client = _client()
    assert client._http.verify == str(eliza_ca)


def test_eliza_session_honors_requests_ca_bundle_via_default_verify(
    tmp_path, monkeypatch
):
    ca_path = tmp_path / "requests-ca.pem"
    ca_path.write_text("bundle", encoding="utf-8")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca_path))
    monkeypatch.delenv("YDBDOC_ELIZA_CA_BUNDLE", raising=False)

    client = _client()
    assert client._http.verify is True


def test_eliza_missing_ca_bundle_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("YDBDOC_ELIZA_CA_BUNDLE", str(tmp_path / "missing.pem"))

    with pytest.raises(LLMConfigError, match="YDBDOC_ELIZA_CA_BUNDLE"):
        _client()


def test_eliza_from_config_uses_shared_usage_tracker(monkeypatch):
    monkeypatch.setenv("YDBDOC_MODEL_PROVIDER", "eliza")
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    shared = UsageTracker()

    client = ElizaLLMClient.from_config(cfg, usage_tracker=shared)

    assert client.usage_tracker is shared

    with patch.object(client._http, "post") as post:
        post.return_value = _resp(
            200,
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )
        client.chat(
            [{"role": "user", "content": "x"}],
            role="translate",
            model="deepseek-v4-flash",
        )

    assert len(shared.records) == 1
    assert shared.records[0].model_slug == "deepseek-v4-flash"
    assert shared.records[0].input_tokens == 10


def test_create_llm_client_eliza_uses_shared_usage_tracker(monkeypatch):
    monkeypatch.setenv("YDBDOC_MODEL_PROVIDER", "eliza")
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    shared = UsageTracker()

    client = create_llm_client(cfg, usage_tracker=shared)

    assert client.usage_tracker is shared


def test_eliza_analyze_role_raises_without_yandex_slug():
    client = _client()

    with pytest.raises(LLMConfigError, match='role "analyze" has no internal Eliza model'):
        client.model_chain_for_role("analyze")

    with pytest.raises(LLMConfigError, match='role "analyze" has no internal Eliza model'):
        client.chat([{"role": "user", "content": "x"}], role="analyze")


def test_eliza_call_once_guard():
    client = _client()

    with pytest.raises(LLMConfigError, match="requests.Session"):
        client._call_once(
            slug="deepseek-v4-flash",
            messages=[{"role": "user", "content": "x"}],
            temperature=0.0,
            max_tokens=100,
            retries=0,
            started=0.0,
            role="translate",
        )


def test_eliza_public_surface_translate_critic_usage():
    client = _client()

    assert client.model_uri("deepseek-v4-flash") == "deepseek-v4-flash"
    assert client.model_chain_for_role("translate") == ["deepseek-v4-flash"]
    assert client.model_chain_for_role("critic") == ["gpt-oss-120b"]
    assert client.usage_tracker.records == []

    with patch.object(client._http, "post") as post:
        post.return_value = _resp(
            200,
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )
        out = client.chat(
            [{"role": "user", "content": "x"}],
            role="translate",
            model="deepseek-v4-flash",
        )

    assert out.content == "ok"
    assert len(client.usage_tracker.records) == 1
