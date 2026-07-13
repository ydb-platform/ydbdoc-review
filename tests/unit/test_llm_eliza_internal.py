from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.client import ElizaLLMClient


def _resp(status: int, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status,
        text=str(payload),
        json=lambda: payload,
    )


def test_eliza_internal_builds_url_with_model_in_path_and_oauth_header(monkeypatch):
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    client = ElizaLLMClient(api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm)

    with patch("ydbdoc_review.llm.client.requests.post") as post:
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


def test_eliza_internal_retries_on_503(monkeypatch):
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "t"})
    # Fast retries for unit test.
    cfg.llm.retries.max_attempts = 2
    cfg.llm.retries.backoff_initial_s = 0.0
    cfg.llm.retries.backoff_factor = 1.0

    client = ElizaLLMClient(api_root="https://api.eliza.yandex.net", oauth_token="t", llm=cfg.llm)

    with (
        patch("ydbdoc_review.llm.client.requests.post") as post,
        patch("time.sleep") as sleep,
    ):
        post.side_effect = [
            _resp(503, {"error": "Service unavailable"}),
            _resp(200, {"choices": [{"message": {"content": "ok"}}]}),
        ]
        out = client.chat([{"role": "user", "content": "x"}], role="translate", model="deepseek-v4-flash")

    assert out.content == "ok"
    assert post.call_count == 2

