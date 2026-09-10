"""F-011: one daily budget admission before paid work."""

from ydbdoc_review.ops.lifecycle import begin_ops_job, finish_ops_job
from ydbdoc_review.ops.runs import InMemoryRunsLedger
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


class CountingLedger(InMemoryRunsLedger):
    def __init__(self, spent: float) -> None:
        super().__init__()
        self.spent = spent
        self.sum_calls = 0

    def sum_cost_for_day(self, run_day: str) -> float:
        self.sum_calls += 1
        return self.spent


def _env() -> dict[str, str]:
    return {
        "GITHUB_ACTOR": "worker",
        "YDBDOC_DAILY_BUDGET_RUB": "100",
        "YDBDOC_TRANSCRIPT_BACKEND": "memory",
    }


def test_F011_threshold() -> None:
    for spent in (100.0, 101.0):
        ledger = CountingLedger(spent)
        ctx, gate, _comment = begin_ops_job(
            mode="translate",
            repo="o/r",
            source_pr=11,
            env=_env(),
            ledger=ledger,
            store=InMemoryTranscriptStore(),
        )
        assert ctx is None
        assert not gate.ok
        assert gate.status == "denied_quota"

    ledger = CountingLedger(99.0)
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=11,
        env=_env(),
        ledger=ledger,
        store=InMemoryTranscriptStore(),
    )
    assert ctx is not None
    assert gate.ok
    assert comment is None


def test_F011_admitted_job() -> None:
    ledger = CountingLedger(99.0)
    store = InMemoryTranscriptStore()
    ctx, gate, _comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=11,
        env=_env(),
        ledger=ledger,
        store=store,
    )
    assert ctx is not None and gate.ok

    finish_ops_job(ctx, status="fallback_qa_ok", cost_rub=1.0)

    assert ledger.sum_calls == 1
    assert ledger.records[-1].cost_rub == 1.0
    assert store.exists_run(ctx.run_id)
