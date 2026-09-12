"""F-016 provider, model-chain, and secret-boundary contracts."""

from __future__ import annotations

import logging

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.client import ElizaLLMClient, YandexLLMClient
from ydbdoc_review.llm.errors import LLMConfigError
from ydbdoc_review.llm.retry import sanitize_llm_error_text
from ydbdoc_review.llm.role_chains import ensure_disjoint_translate_critic_chains
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.chunker import Batch
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.prompts import build_translate_messages


def test_F016_model_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "YDBDOC_MODEL_TRANSLATE",
        "YDBDOC_MODEL_CHECK",
        "YDBDOC_ELIZA_TRANSLATE_FALLBACKS",
        "YDBDOC_ELIZA_CHECK_FALLBACKS",
    ):
        monkeypatch.delenv(name, raising=False)

    assert ensure_disjoint_translate_critic_chains(
        ["translate", "translate-fallback"], ["critic", "critic-fallback"]
    ) == (["translate", "translate-fallback"], ["critic", "critic-fallback"])

    with pytest.raises(LLMConfigError, match="fallback"):
        ensure_disjoint_translate_critic_chains(["translate"], ["critic"])
    with pytest.raises(LLMConfigError, match="duplicate"):
        ensure_disjoint_translate_critic_chains(
            ["translate", "translate"], ["critic", "critic-fallback"]
        )
    with pytest.raises(LLMConfigError, match="overlap"):
        ensure_disjoint_translate_critic_chains(
            ["translate", "shared"], ["critic", "shared"]
        )

    yandex_cfg = load_config(
        env={"YDBDOC_YC_FOLDER_ID": "folder", "YDBDOC_YC_API_KEY": "key"}
    )
    yandex = YandexLLMClient.from_config(yandex_cfg)
    assert len(yandex.model_chain_for_role("translate")) >= 2

    eliza_cfg = load_config(env={"ELIZA_OAUTH_TOKEN": "token"})
    eliza = ElizaLLMClient(
        api_root="https://api.eliza.yandex.net",
        oauth_token="token",
        llm=eliza_cfg.llm,
    )
    assert len(eliza.model_chain_for_role("translate")) >= 2


def test_F016_secrets(caplog: pytest.LogCaptureFixture) -> None:
    secret = "control-secret-f016"
    cfg = load_config(env={"ELIZA_OAUTH_TOKEN": secret})
    segment = extract_segments(parse_markdown("Привет\n"))[0]
    messages = build_translate_messages(
        Batch(index=0, segments=[segment]),
        load_glossary(),
        file_path="docs/ru/example.md",
    )

    caplog.set_level(logging.WARNING)
    sanitized = sanitize_llm_error_text(
        f"request failed with {secret}", redact=secret
    )
    prompt_text = " ".join(str(message) for message in messages)

    assert secret not in sanitized
    assert secret not in prompt_text
    assert secret not in repr(cfg.model_dump(exclude={"secrets"}))
    assert secret not in caplog.text
