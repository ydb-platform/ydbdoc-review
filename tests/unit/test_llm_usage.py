"""Tests for usage tracking."""

from __future__ import annotations

import pytest

from ydbdoc_review.llm.usage import LLMUsage, UsageTracker


def test_usage_tracker_totals_skip_failed():
    tracker = UsageTracker()
    tracker.add(LLMUsage("yandexgpt-5.1", 1000, 500, 100.0, 0, True))
    tracker.add(LLMUsage("yandexgpt-5.1", 999, 999, 50.0, 1, False))
    assert tracker.total_input_tokens == 1000
    assert tracker.total_output_tokens == 500


def test_estimate_cost_usd_known_model():
    tracker = UsageTracker()
    tracker.add(LLMUsage("yandexgpt-5.1", 1_000_000, 1_000_000, 0.0, 0, True))
    assert tracker.estimate_cost_usd() == pytest.approx(0.80)


def test_estimate_cost_usd_unknown_model():
    tracker = UsageTracker()
    tracker.add(LLMUsage("unknown-model", 1_000_000, 1_000_000, 0.0, 0, True))
    assert tracker.estimate_cost_usd() == 0.0


def test_tokens_for_role_and_retries():
    tracker = UsageTracker()
    tracker.add(LLMUsage("yandexgpt-5.1", 100, 50, 10.0, 2, True, role="translate"))
    tracker.add(LLMUsage("qwen3.6-35b-a3b", 200, 80, 10.0, 0, True, role="critic"))
    assert tracker.tokens_for_role("translate") == (100, 50)
    assert tracker.tokens_for_role("critic") == (200, 80)
    assert tracker.total_retry_count == 2
    assert tracker.models_for_role("translate") == ["yandexgpt-5.1"]
