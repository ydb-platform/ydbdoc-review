"""Live smoke test against Yandex AI Studio. Local only — not for CI."""

from __future__ import annotations

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm import YandexLLMClient, parse_json_content

pytestmark = pytest.mark.llm


def _has_credentials() -> bool:
    cfg = load_config()
    return bool(cfg.secrets.yc_folder_id and cfg.secrets.yc_api_key)


@pytest.fixture
def llm_client() -> YandexLLMClient:
    if not _has_credentials():
        pytest.skip("Yandex credentials not configured (YDBDOC_YC_* or v1 aliases)")
    return YandexLLMClient.from_config(load_config())


@pytest.mark.skipif(not _has_credentials(), reason="no Yandex credentials")
def test_smoke_plain_translation(llm_client: YandexLLMClient) -> None:
    result = llm_client.chat(
        [
            {"role": "system", "content": "You are a professional technical translator."},
            {
                "role": "user",
                "content": "Translate to English: Используйте параметризованные запросы.",
            },
        ],
        role="translate",
        max_tokens=256,
    )
    assert result.content.strip()
    assert "parameter" in result.content.lower() or "query" in result.content.lower()
    assert result.usage.success


@pytest.mark.skipif(not _has_credentials(), reason="no Yandex credentials")
def test_smoke_json_translation(llm_client: YandexLLMClient) -> None:
    system = (
        'Return ONLY a JSON object: {"translations": [{"id": "s0001", "text": "..."}]}'
        " No markdown fences."
    )
    user = '{"segments": [{"id": "s0001", "text": "Привет, мир."}]}'
    result = llm_client.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        role="translate",
        max_tokens=512,
    )
    parsed = parse_json_content(result.content)
    assert parsed["translations"][0]["id"] == "s0001"
    assert parsed["translations"][0]["text"]
