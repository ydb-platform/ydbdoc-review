"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _default_yandex_model_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate unit tests from developer shell Eliza/Yandex overrides."""
    monkeypatch.setenv("YDBDOC_MODEL_PROVIDER", "yandex_cloud")
    monkeypatch.delenv("YDBDOC_ELIZA_TRANSLATE_FALLBACKS", raising=False)
    monkeypatch.delenv("YDBDOC_ELIZA_CHECK_FALLBACKS", raising=False)
    monkeypatch.delenv("YDBDOC_ELIZA_CRITIC_FALLBACKS", raising=False)
    monkeypatch.delenv("YDBDOC_MODEL_TRANSLATE", raising=False)
    monkeypatch.delenv("YDBDOC_MODEL_CHECK", raising=False)
