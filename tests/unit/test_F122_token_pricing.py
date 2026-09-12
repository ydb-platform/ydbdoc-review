import pytest

from ydbdoc_review.llm.usage import (
    LLMUsage,
    UsageTracker,
    validate_model_pricing,
)


def test_F122_separate_rates() -> None:
    record = LLMUsage(
        "deepseek-v4-flash", 2_000, 500, 1.0, 0, True, role="analyze"
    )
    tracker = UsageTracker([record])

    assert tracker.estimate_cost_rub() == pytest.approx(0.85)
    assert record.input_tokens == 2_000
    assert record.output_tokens == 500
    assert record.model_slug == "deepseek-v4-flash"


def test_F122_unknown_values() -> None:
    with pytest.raises(ValueError, match="no configured token price"):
        validate_model_pricing("unknown-model")

    tracker = UsageTracker(
        [LLMUsage("unknown-model", 100, 50, 1.0, 0, True, role="translate")]
    )
    assert tracker.estimate_cost_rub() == 0.0
    assert tracker.is_cost_unknown()
