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


@pytest.fixture(autouse=True)
def _isolate_actions_event_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit workflows must not inherit the hosting CI run as a product event."""
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_EVENT_ID", "GITHUB_SHA"):
        monkeypatch.delenv(name, raising=False)
