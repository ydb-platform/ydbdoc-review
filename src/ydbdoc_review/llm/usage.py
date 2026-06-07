"""Token usage tracking and rough cost estimation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ydbdoc_review.llm.usage import UsageTracker
    from ydbdoc_review.translation.glossary import Glossary

# RUB per 1K tokens (input, output), sync mode incl. VAT — Yandex AI Studio.
# Updated manually; the API does not return prices in responses.
MODEL_PRICE_RUB_PER_1K: dict[str, tuple[float, float]] = {
    "yandexgpt-5.1": (0.80, 0.80),
    "yandexgpt-5-pro": (0.80, 0.80),
    "yandexgpt-5-lite": (0.20, 0.20),
    "deepseek-v32": (0.50, 0.40),
    "deepseek-v4-flash": (0.30, 0.50),
    "qwen3.6-35b-a3b": (0.50, 0.40),
    "qwen3-235b-a22b-fp8": (0.80, 0.80),
    "gpt-oss-120b": (0.20, 0.20),
    "gpt-oss-20b": (0.10, 0.10),
}


def _estimate_cost_rub(records: list[LLMUsage]) -> float:
    total = 0.0
    for record in records:
        if not record.success:
            continue
        prices = MODEL_PRICE_RUB_PER_1K.get(record.model_slug)
        if prices is None:
            continue
        in_price, out_price = prices
        total += (record.input_tokens or 0) / 1_000 * in_price
        total += (record.output_tokens or 0) / 1_000 * out_price
    return total


@dataclass(frozen=True)
class LLMUsage:
    """Metrics for a single successful or failed chat completion attempt."""

    model_slug: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    retries: int
    success: bool
    role: LLMRole | None = None


@dataclass
class UsageTracker:
    """Accumulates per-call usage records for a run (PR or session)."""

    records: list[LLMUsage] = field(default_factory=list)

    def add(self, record: LLMUsage) -> None:
        self.records.append(record)

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens or 0 for r in self.records if r.success)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens or 0 for r in self.records if r.success)

    def estimate_cost_rub(self, *, since: int = 0) -> float:
        """Rough RUB cost from the hard-coded Yandex AI Studio price table."""
        return _estimate_cost_rub(self.records[since:])

    def estimate_cost_usd(self, *, since: int = 0) -> float:
        """Deprecated alias — returns RUB estimate (kept for callers)."""
        return self.estimate_cost_rub(since=since)

    def metrics_since(self, record_index: int = 0) -> dict[str, float | int | list[str]]:
        """Token/cost totals for records appended after ``record_index``."""
        records = self.records[record_index:]
        models = sorted({r.model_slug for r in records if r.success})
        return {
            "input_tokens": sum(r.input_tokens or 0 for r in records if r.success),
            "output_tokens": sum(r.output_tokens or 0 for r in records if r.success),
            "estimated_cost_usd": _estimate_cost_rub(records),
            "models_used": models,
        }

    def tokens_for_role(self, role: LLMRole) -> tuple[int, int]:
        """Return (input_tokens, output_tokens) for successful calls with ``role``."""
        inp = out = 0
        for record in self.records:
            if record.success and record.role == role:
                inp += record.input_tokens or 0
                out += record.output_tokens or 0
        return inp, out

    @property
    def total_retry_count(self) -> int:
        """Sum of per-call retry counters (failed attempts before success)."""
        return sum(record.retries for record in self.records)

    def models_for_role(self, role: LLMRole) -> list[str]:
        """Distinct model slugs used successfully for a role."""
        seen: set[str] = set()
        out: list[str] = []
        for record in self.records:
            if record.success and record.role == role and record.model_slug not in seen:
                seen.add(record.model_slug)
                out.append(record.model_slug)
        return out
