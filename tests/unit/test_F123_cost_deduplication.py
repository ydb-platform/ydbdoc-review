from datetime import UTC, datetime

from ydbdoc_review.llm.usage import LLMUsage, UsageTracker
from ydbdoc_review.ops.runs import InMemoryRunsLedger, RunRecord


def _run(run_id: str, cost: float) -> RunRecord:
    return RunRecord(
        run_day="2026-09-13",
        run_id=run_id,
        actor="sintjuri",
        mode="continue",
        repo="o/r",
        source_pr=123,
        status="ok",
        cost_rub=cost,
        started_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
    )


def test_F123_dimensions() -> None:
    usage = UsageTracker(
        [
            LLMUsage("deepseek-v4-flash", 1_000, 1_000, 1.0, 0, True, "critic"),
            LLMUsage("yandexgpt-5.1", 1_000, 500, 1.0, 0, True, "translate"),
        ]
    )
    ledger = InMemoryRunsLedger()
    ledger.upsert_run(_run("inline-verify", usage.estimate_cost_rub()))

    assert usage.tokens_for_role("critic") == (1_000, 1_000)
    assert usage.tokens_for_role("translate") == (1_000, 500)
    assert ledger.sum_cost_for_day("2026-09-13") == usage.estimate_cost_rub()


def test_F123_repeat_save() -> None:
    ledger = InMemoryRunsLedger()
    ledger.upsert_run(_run("run-1", 1.25))
    ledger.upsert_run(_run("run-1", 1.25))

    assert ledger.sum_cost_for_day("2026-09-13") == 1.25
