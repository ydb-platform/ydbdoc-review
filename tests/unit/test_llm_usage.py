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


def test_estimate_cost_rub_known_model():
    tracker = UsageTracker()
    tracker.add(LLMUsage("yandexgpt-5.1", 1_000, 1_000, 0.0, 0, True))
    assert tracker.estimate_cost_rub() == pytest.approx(1.60)


def test_estimate_cost_rub_unknown_model():
    tracker = UsageTracker()
    tracker.add(LLMUsage("unknown-model", 1_000, 1_000, 0.0, 0, True))
    assert tracker.estimate_cost_rub() == 0.0
    assert tracker.is_cost_unknown()
    assert tracker.unpriced_models() == ["unknown-model"]


def test_estimate_cost_rub_eliza_models():
    tracker = UsageTracker()
    tracker.add(
        LLMUsage("deepseek-v4-flash", 1_000, 1_000, 0.0, 0, True, role="translate")
    )
    tracker.add(
        LLMUsage("gpt-oss-120b", 500, 200, 0.0, 0, True, role="critic")
    )
    assert tracker.estimate_cost_rub() == pytest.approx(0.94)
    assert not tracker.is_cost_unknown()


def test_estimate_cost_rub_null_output_tokens():
    tracker = UsageTracker()
    tracker.add(LLMUsage("yandexgpt-5.1", 1_000, None, 0.0, 0, True))  # type: ignore[arg-type]
    assert tracker.estimate_cost_rub() == pytest.approx(0.80)


def test_metrics_since_per_file_slice():
    tracker = UsageTracker()
    tracker.add(LLMUsage("deepseek-v32", 100, 50, 10.0, 0, True, role="translate"))
    start = len(tracker.records)
    tracker.add(LLMUsage("deepseek-v32", 200, 80, 10.0, 0, True, role="critic"))
    metrics = tracker.metrics_since(start)
    assert metrics["input_tokens"] == 200
    assert metrics["output_tokens"] == 80
    assert tracker.estimate_cost_usd() > tracker.estimate_cost_usd(since=start)


def test_tokens_for_role_and_retries():
    tracker = UsageTracker()
    tracker.add(LLMUsage("yandexgpt-5.1", 100, 50, 10.0, 2, True, role="translate"))
    tracker.add(LLMUsage("qwen3.6-35b-a3b", 200, 80, 10.0, 0, True, role="critic"))
    assert tracker.tokens_for_role("translate") == (100, 50)
    assert tracker.tokens_for_role("critic") == (200, 80)
    assert tracker.total_retry_count == 2
    assert tracker.models_for_role("translate") == ["yandexgpt-5.1"]
