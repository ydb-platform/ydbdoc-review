"""F-014: paid work requires a readable budget ledger."""

from __future__ import annotations

from ydbdoc_review.ops.lifecycle import begin_ops_job, finish_ops_job
from ydbdoc_review.ops.runs import InMemoryRunsLedger, RunRecord


class UnavailableLedger(InMemoryRunsLedger):
    def sum_cost_for_day(self, run_day: str) -> float:
        raise RuntimeError("ledger read failed")


class LateWriteFailureLedger(InMemoryRunsLedger):
    def upsert_run(self, record: RunRecord) -> None:
        raise RuntimeError("ledger write failed")


def test_F014_read_failure() -> None:
    paid_calls: list[str] = []
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="ydb-platform/ydb",
        source_pr=123,
        env={"YDBDOC_DAILY_BUDGET_RUB": "100"},
        ledger=UnavailableLedger(),
    )

    assert ctx is None
    assert gate.status == "denied_accounting"
    assert "учёт дневного бюджета недоступен" in (comment or "")
    assert "исчерпан" not in (comment or "")

    if ctx is not None:
        paid_calls.append("llm")
    assert paid_calls == []


def test_F014_late_write_failure() -> None:
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="ydb-platform/ydb",
        source_pr=123,
        env={"YDBDOC_DAILY_BUDGET_RUB": "100"},
        ledger=LateWriteFailureLedger(),
    )

    assert ctx is not None
    assert gate.ok
    assert gate.status == "ok"
    assert comment is None

    finish_ops_job(ctx, status="ok", cost_rub=10.0)

    assert gate.ok
    assert gate.status == "ok"
