from __future__ import annotations

from pathlib import Path
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
        patch("ydbdoc_review.llm.client.interruptible_sleep") as sleep,
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
        patch("ydbdoc_review.llm.client.interruptible_sleep") as sleep,
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
        patch("ydbdoc_review.llm.client.interruptible_sleep") as sleep,
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
        patch("ydbdoc_review.llm.client.interruptible_sleep") as sleep,
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
        patch("ydbdoc_review.llm.client.interruptible_sleep") as sleep,
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
        patch("ydbdoc_review.llm.client.interruptible_sleep") as sleep,
    ):
        post.side_effect = [
            _resp(429, {"error": "rate limit exceeded"}, headers={"Retry-After": "5"}),
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
        patch("ydbdoc_review.llm.client.interruptible_sleep"),
    ):
        post.return_value = _resp(
            429, {"error": "rate limit exceeded"}, headers={"Retry-After": "1"}
        )
        with pytest.raises(LLMRetryExhaustedError, match="rate-limit \\(429\\)"):
            client.chat(
                [{"role": "user", "content": "x"}],
                role="translate",
                model="deepseek-v4-flash",
            )

    assert post.call_count == 4


def test_eliza_429_overloaded_fails_fast_on_single_model():
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    cfg.llm.retries.rate_limit.max_attempts = 6
    cfg.llm.retries.rate_limit.backoff_initial_s = 0.0
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("ydbdoc_review.llm.client.interruptible_sleep"),
    ):
        post.return_value = _resp(
            429, {"error": "model deepseek-v4-flash is overloaded"}
        )
        with pytest.raises(LLMRetryExhaustedError, match="rate-limit \\(429\\)"):
            client.chat(
                [{"role": "user", "content": "x"}],
                role="translate",
                model="deepseek-v4-flash",
            )

    assert post.call_count == 1


def test_eliza_429_after_retries_switches_to_next_model_in_chain(monkeypatch):
    monkeypatch.setenv("YDBDOC_MODEL_TRANSLATE", "deepseek-v4-flash")
    monkeypatch.setenv("YDBDOC_ELIZA_TRANSLATE_FALLBACKS", "translate-fallback-model")
    monkeypatch.setenv("YDBDOC_MODEL_CHECK", "gpt-oss-120b")
    monkeypatch.delenv("YDBDOC_ELIZA_CHECK_FALLBACKS", raising=False)
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    cfg.llm.retries.rate_limit.max_attempts = 2
    cfg.llm.retries.rate_limit.backoff_initial_s = 0.0
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("ydbdoc_review.llm.client.interruptible_sleep"),
    ):
        post.side_effect = [
            _resp(429, {"error": "rate limit exceeded"}),
            _resp(429, {"error": "rate limit exceeded"}),
            _resp(200, {"choices": [{"message": {"content": "ok"}}]}),
        ]
        out = client.chat([{"role": "user", "content": "x"}], role="translate")

    assert out.content == "ok"
    assert out.model_slug == "translate-fallback-model"
    assert post.call_count == 3


def test_eliza_503_exhausted_switches_to_next_model_in_chain(monkeypatch):
    monkeypatch.setenv("YDBDOC_MODEL_TRANSLATE", "deepseek-v4-flash")
    monkeypatch.setenv("YDBDOC_ELIZA_TRANSLATE_FALLBACKS", "translate-fallback-model")
    monkeypatch.setenv("YDBDOC_MODEL_CHECK", "gpt-oss-120b")
    monkeypatch.delenv("YDBDOC_ELIZA_CHECK_FALLBACKS", raising=False)
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    cfg.llm.retries.max_attempts = 2
    cfg.llm.retries.backoff_initial_s = 0.0
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("ydbdoc_review.llm.client.interruptible_sleep"),
    ):
        post.side_effect = [
            _resp(503, {"error": "Service unavailable"}),
            _resp(503, {"error": "Service unavailable"}),
            _resp(200, {"choices": [{"message": {"content": "ok"}}]}),
        ]
        out = client.chat([{"role": "user", "content": "x"}], role="translate")

    assert out.model_slug == "translate-fallback-model"
    assert post.call_count == 3


def test_eliza_4xx_does_not_advance_model_chain(monkeypatch):
    monkeypatch.setenv("YDBDOC_MODEL_TRANSLATE", "deepseek-v4-flash")
    monkeypatch.setenv("YDBDOC_ELIZA_TRANSLATE_FALLBACKS", "translate-fallback-model")
    monkeypatch.setenv("YDBDOC_MODEL_CHECK", "gpt-oss-120b")
    monkeypatch.delenv("YDBDOC_ELIZA_CHECK_FALLBACKS", raising=False)
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with patch.object(client._http, "post") as post:
        post.return_value = _resp(403, {"error": "forbidden"})
        with pytest.raises(LLMRequestError, match="403"):
            client.chat([{"role": "user", "content": "x"}], role="translate")

    assert post.call_count == 1
    assert "translate-fallback-model" not in post.call_args[0][0]


def test_eliza_parse_error_does_not_advance_model_chain(monkeypatch):
    monkeypatch.setenv("YDBDOC_MODEL_TRANSLATE", "deepseek-v4-flash")
    monkeypatch.setenv("YDBDOC_ELIZA_TRANSLATE_FALLBACKS", "translate-fallback-model")
    monkeypatch.setenv("YDBDOC_MODEL_CHECK", "gpt-oss-120b")
    monkeypatch.delenv("YDBDOC_ELIZA_CHECK_FALLBACKS", raising=False)
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    cfg.llm.retries.max_attempts = 2
    cfg.llm.retries.backoff_initial_s = 0.0
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("ydbdoc_review.llm.client.interruptible_sleep"),
    ):
        post.side_effect = [
            _resp(200, {"choices": []}),
            _resp(200, {"choices": []}),
        ]
        with pytest.raises(LLMRetryExhaustedError, match="deepseek-v4-flash"):
            client.chat([{"role": "user", "content": "x"}], role="translate")

    assert post.call_count == 2
    assert all(
        call[0][0].endswith("/raw/internal/deepseek-v4-flash/v1/chat/completions")
        for call in post.call_args_list
    )


def test_eliza_full_chain_429_raises_all_model_names(monkeypatch):
    monkeypatch.setenv("YDBDOC_MODEL_TRANSLATE", "deepseek-v4-flash")
    monkeypatch.setenv("YDBDOC_ELIZA_TRANSLATE_FALLBACKS", "translate-fallback-model")
    monkeypatch.setenv("YDBDOC_MODEL_CHECK", "gpt-oss-120b")
    monkeypatch.delenv("YDBDOC_ELIZA_CHECK_FALLBACKS", raising=False)
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    cfg.llm.retries.rate_limit.max_attempts = 1
    cfg.llm.retries.rate_limit.backoff_initial_s = 0.0
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with (
        patch.object(client._http, "post") as post,
        patch("ydbdoc_review.llm.client.interruptible_sleep"),
    ):
        post.return_value = _resp(429, {"error": "model is overloaded"})
        with pytest.raises(LLMRetryExhaustedError) as exc_info:
            client.chat([{"role": "user", "content": "x"}], role="translate")

    msg = str(exc_info.value)
    assert "deepseek-v4-flash" in msg
    assert "translate-fallback-model" in msg
    assert post.call_count == 2


def test_eliza_429_overloaded_switches_to_next_model_in_chain(monkeypatch):
    monkeypatch.setenv("YDBDOC_MODEL_TRANSLATE", "deepseek-v4-flash")
    monkeypatch.setenv("YDBDOC_ELIZA_TRANSLATE_FALLBACKS", "translate-fallback-model")
    monkeypatch.setenv("YDBDOC_MODEL_CHECK", "gpt-oss-120b")
    monkeypatch.delenv("YDBDOC_ELIZA_CHECK_FALLBACKS", raising=False)
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    client = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm
    )

    with patch.object(client._http, "post") as post:
        post.side_effect = [
            _resp(429, {"error": "model deepseek-v4-flash is overloaded"}),
            _resp(200, {"choices": [{"message": {"content": "ok"}}]}),
        ]
        out = client.chat([{"role": "user", "content": "x"}], role="translate")

    assert out.content == "ok"
    assert out.model_slug == "translate-fallback-model"
    assert post.call_count == 2
    assert post.call_args_list[1][0][0].endswith(
        "/raw/internal/translate-fallback-model/v1/chat/completions"
    )


def test_eliza_translate_chain_env_primary_and_fallbacks(monkeypatch):
    monkeypatch.setenv("YDBDOC_MODEL_TRANSLATE", "deepseek-v4-flash")
    # Fallbacks must not reuse critic primary (§6.127)
    monkeypatch.setenv("YDBDOC_ELIZA_TRANSLATE_FALLBACKS", "other-translate-fallback")
    monkeypatch.setenv("YDBDOC_MODEL_CHECK", "gpt-oss-120b")
    monkeypatch.delenv("YDBDOC_ELIZA_CHECK_FALLBACKS", raising=False)
    client = _client()
    assert client.model_chain_for_role("translate") == [
        "deepseek-v4-flash",
        "other-translate-fallback",
    ]


def test_eliza_critic_chain_env_check_fallbacks(monkeypatch):
    monkeypatch.setenv("YDBDOC_MODEL_CHECK", "gpt-oss-120b")
    monkeypatch.setenv("YDBDOC_ELIZA_CHECK_FALLBACKS", "other-critic-fallback")
    monkeypatch.setenv("YDBDOC_MODEL_TRANSLATE", "deepseek-v4-flash")
    monkeypatch.delenv("YDBDOC_ELIZA_TRANSLATE_FALLBACKS", raising=False)
    client = _client()
    assert client.model_chain_for_role("critic") == [
        "gpt-oss-120b",
        "other-critic-fallback",
    ]


def test_eliza_translate_chain_uses_yaml_eliza_defaults(monkeypatch):
    monkeypatch.delenv("YDBDOC_MODEL_TRANSLATE", raising=False)
    monkeypatch.delenv("YDBDOC_ELIZA_TRANSLATE_FALLBACKS", raising=False)
    monkeypatch.delenv("YDBDOC_MODEL_CHECK", raising=False)
    monkeypatch.delenv("YDBDOC_ELIZA_CHECK_FALLBACKS", raising=False)
    client = _client()
    assert client.model_chain_for_role("translate") == ["deepseek-v4-flash"]
    assert client.model_chain_for_role("critic") == ["gpt-oss-120b"]


def test_eliza_strips_overlapping_translate_fallback_into_critic(monkeypatch):
    monkeypatch.setenv("YDBDOC_MODEL_TRANSLATE", "deepseek-v4-flash")
    monkeypatch.setenv("YDBDOC_ELIZA_TRANSLATE_FALLBACKS", "gpt-oss-120b")
    monkeypatch.setenv("YDBDOC_MODEL_CHECK", "gpt-oss-120b")
    monkeypatch.delenv("YDBDOC_ELIZA_CHECK_FALLBACKS", raising=False)
    client = _client()
    assert client.model_chain_for_role("translate") == ["deepseek-v4-flash"]
    assert client.model_chain_for_role("critic") == ["gpt-oss-120b"]


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
        patch("ydbdoc_review.llm.client.interruptible_sleep"),
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
        patch("ydbdoc_review.llm.client.interruptible_sleep"),
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
        patch("ydbdoc_review.llm.client.interruptible_sleep"),
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
    ca_path.write_text(
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("YDBDOC_ELIZA_CA_BUNDLE", str(ca_path))

    client = _client()
    assert client._http.verify not in (True, False)
    assert Path(str(client._http.verify)).is_file()


def test_eliza_session_ydbdoc_ca_overrides_requests_env(tmp_path, monkeypatch):
    eliza_ca = tmp_path / "eliza-ca.pem"
    eliza_ca.write_text(
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    other_ca = tmp_path / "other-ca.pem"
    other_ca.write_text("other", encoding="utf-8")
    monkeypatch.setenv("YDBDOC_ELIZA_CA_BUNDLE", str(eliza_ca))
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(other_ca))

    client = _client()
    assert client._http.verify != str(other_ca)
    assert client._http.verify != str(eliza_ca)


def test_eliza_session_ignores_requests_ca_bundle_for_eliza_only(
    tmp_path, monkeypatch
):
    ca_path = tmp_path / "requests-ca.pem"
    ca_path.write_text("bundle", encoding="utf-8")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca_path))
    monkeypatch.delenv("YDBDOC_ELIZA_CA_BUNDLE", raising=False)
    monkeypatch.setattr(
        "ydbdoc_review.llm.tls._DEFAULT_INTERNAL_CA",
        str(tmp_path / "missing-internal.pem"),
    )

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
