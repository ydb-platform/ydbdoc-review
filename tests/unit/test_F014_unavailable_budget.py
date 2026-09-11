"""F-014: unavailable budget accounting fails closed before paid work."""

import pytest

from ydbdoc_review.ops.lifecycle import begin_ops_job, finish_ops_job
from ydbdoc_review.ops.runs import InMemoryRunsLedger
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


class ReadFailLedger(InMemoryRunsLedger):
    def sum_cost_for_day(self, run_day: str) -> float:
        raise RuntimeError("ledger read failed")


class LateWriteFailLedger(InMemoryRunsLedger):
    def upsert_run(self, record: object) -> None:
        raise RuntimeError("ledger write failed")


def _env() -> dict[str, str]:
    return {
        "GITHUB_ACTOR": "worker",
        "YDBDOC_DAILY_BUDGET_RUB": "100",
        "YDBDOC_TRANSCRIPT_BACKEND": "memory",
    }


def test_F014_read_failure() -> None:
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=14,
        env=_env(),
        ledger=ReadFailLedger(),
        store=InMemoryTranscriptStore(),
    )

    assert ctx is None
    assert not gate.ok
    assert gate.status == "denied_accounting"
    assert comment is not None
    assert "учёт" in comment
    assert "квот" not in comment


def test_F014_late_write_failure() -> None:
    ledger = LateWriteFailLedger()
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=14,
        env=_env(),
        ledger=ledger,
        store=InMemoryTranscriptStore(),
    )

    assert ctx is not None
    assert gate.ok
    assert comment is None

    try:
        finish_ops_job(ctx, status="ok", cost_rub=10.0)
    except Exception as exc:  # pragma: no cover - documents the contract
        pytest.fail(f"late ledger write failure must not cancel admitted work: {exc}")

    assert gate.ok
    assert ctx is not None
