"""Token usage tracking and rough cost estimation."""

from __future__ import annotations

from dataclasses import dataclass, field

# USD per 1M tokens (input, output). Updated manually — Yandex does not
# expose prices in API responses.
MODEL_PRICE_USD_PER_1M: dict[str, tuple[float, float]] = {
    "yandexgpt-5.1": (0.40, 0.40),
    "yandexgpt-5-pro": (0.40, 0.40),
    "yandexgpt-5-lite": (0.20, 0.20),
    "deepseek-v32": (0.30, 0.30),
    "qwen3.6-35b-a3b": (0.25, 0.25),
    "qwen3-235b-a22b-fp8": (0.50, 0.50),
    "gpt-oss-120b": (0.20, 0.20),
    "gpt-oss-20b": (0.10, 0.10),
}


@dataclass(frozen=True)
class LLMUsage:
    """Metrics for a single successful or failed chat completion attempt."""

    model_slug: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    retries: int
    success: bool


@dataclass
class UsageTracker:
    """Accumulates per-call usage records for a run (PR or session)."""

    records: list[LLMUsage] = field(default_factory=list)

    def add(self, record: LLMUsage) -> None:
        self.records.append(record)

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.records if r.success)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.records if r.success)

    def estimate_cost_usd(self) -> float:
        """Rough USD cost from the hard-coded price table."""
        total = 0.0
        for record in self.records:
            if not record.success:
                continue
            prices = MODEL_PRICE_USD_PER_1M.get(record.model_slug)
            if prices is None:
                continue
            in_price, out_price = prices
            total += record.input_tokens / 1_000_000 * in_price
            total += record.output_tokens / 1_000_000 * out_price
        return total
