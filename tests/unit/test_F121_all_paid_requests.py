import pytest

from ydbdoc_review.llm.usage import LLMUsage, UsageTracker


def test_F121_all_roles() -> None:
    tracker = UsageTracker()
    for role, model in (
        ("analyze", "yandexgpt-5.1"),
        ("translate", "yandexgpt-5.1"),
        ("critic", "deepseek-v4-flash"),
        ("repair", "gpt-oss-120b"),
        ("continue", "qwen3.6-35b-a3b"),
    ):
        tracker.add(LLMUsage(model, 100, 50, 1.0, 0, True, role=role))
    tracker.add(
        LLMUsage(
            "yandexgpt-5.1",
            25,
            10,
            1.0,
            1,
            False,
            role="analyze",
        )
    )

    assert tracker.total_input_tokens == 525
    assert tracker.total_output_tokens == 260
    assert tracker.estimate_cost_rub() > 0
    assert tracker.tokens_for_role("analyze") == (125, 60)


def test_F121_no_artifact() -> None:
    tracker = UsageTracker()
    tracker.add(LLMUsage("yandexgpt-5.1", 200, 0, 1.0, 0, True, role="analyze"))

    metrics = tracker.metrics_since()
    assert metrics["input_tokens"] == 200
    assert metrics["output_tokens"] == 0
    assert metrics["models_used"] == ["yandexgpt-5.1"]
    assert tracker.estimate_cost_rub() == pytest.approx(0.16)
