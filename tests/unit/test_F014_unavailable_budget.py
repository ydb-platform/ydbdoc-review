"""F-014: unavailable budget accounting must fail closed."""

from ydbdoc_review.ops import lifecycle
from ydbdoc_review.ops.lifecycle import begin_ops_job, finish_ops_job
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


class _UnreadableLedger:
    def sum_cost_for_day(self, _run_day: str) -> float:
        raise RuntimeError("ledger read failed")


class _LateWriteFailureLedger:
    def __init__(self) -> None:
        self.write_attempted = False

    def sum_cost_for_day(self, _run_day: str) -> float:
        return 0.0

    def upsert_run(self, _record: object) -> None:
        self.write_attempted = True
        raise RuntimeError("ledger write failed")


def _env() -> dict[str, str]:
    return {
        "GITHUB_ACTOR": "alice",
        "YDBDOC_ALLOWED_ACTORS": "alice",
        "YDBDOC_DAILY_BUDGET_RUB": "100",
        "YDBDOC_TRANSCRIPT_BACKEND": "memory",
    }


def test_F014_read_failure(monkeypatch):
    monkeypatch.setattr(lifecycle, "msk_today", lambda: "2026-09-12")

    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=14,
        env=_env(),
        ledger=_UnreadableLedger(),
        store=InMemoryTranscriptStore(),
    )

    assert ctx is None
    assert not gate.ok
    assert gate.status == "denied_accounting"
    assert gate.reason == "budget accounting unavailable"
    assert comment is not None
    assert "учёт дневного бюджета недоступен" in comment
    assert "исчерпан" not in comment


def test_F014_late_write_failure(monkeypatch):
    monkeypatch.setattr(lifecycle, "msk_today", lambda: "2026-09-12")
    ledger = _LateWriteFailureLedger()
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=14,
        env=_env(),
        ledger=ledger,
        store=InMemoryTranscriptStore(),
    )

    assert gate.ok and ctx is not None and comment is None
    finish_ops_job(ctx, status="ok", cost_rub=10.0)
    assert ledger.write_attempted
