"""F-012: completed-run costs belong to the admission day."""

from ydbdoc_review.ops import lifecycle
from ydbdoc_review.ops.lifecycle import begin_ops_job, finish_ops_job
from ydbdoc_review.ops.runs import InMemoryRunsLedger
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


def test_F012_midnight(monkeypatch):
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    monkeypatch.setattr(lifecycle, "msk_today", lambda: "2026-09-09")

    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=12,
        env={"YDBDOC_SKIP_OPS_GATES": "1"},
        ledger=ledger,
        store=store,
    )
    assert gate.ok and ctx is not None and comment is None

    monkeypatch.setattr(lifecycle, "msk_today", lambda: "2026-09-10")
    finish_ops_job(ctx, status="ok", cost_rub=75.0)

    assert ledger.sum_cost_for_day("2026-09-09") == 75.0
    assert ledger.sum_cost_for_day("2026-09-10") == 0.0


def test_F012_concurrent_costs(monkeypatch):
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    monkeypatch.setattr(lifecycle, "msk_today", lambda: "2026-09-10")
    env = {
        "GITHUB_ACTOR": "sintjuri",
        "YDBDOC_DAILY_BUDGET_RUB": "100",
        "YDBDOC_TRANSCRIPT_BACKEND": "memory",
    }

    first, first_gate, _ = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=12,
        env=env,
        ledger=ledger,
        store=store,
    )
    second, second_gate, _ = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=12,
        env=env,
        ledger=ledger,
        store=store,
    )
    assert first_gate.ok and first is not None
    assert second_gate.ok and second is not None

    finish_ops_job(first, status="ok", cost_rub=60.0)
    finish_ops_job(second, status="ok", cost_rub=60.0)

    assert ledger.sum_cost_for_day("2026-09-10") == 120.0
