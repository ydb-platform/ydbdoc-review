"""Lifecycle begin/finish with in-memory backends."""

from ydbdoc_review.ops.lifecycle import begin_ops_job, finish_ops_job
from ydbdoc_review.ops.runs import InMemoryRunsLedger
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


def test_begin_acl_deny():
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=1,
        env={
            "YDBDOC_ALLOWED_ACTORS": "sintjuri",
            "GITHUB_ACTOR": "other",
            "YDBDOC_DAILY_BUDGET_RUB": "5000",
        },
        ledger=ledger,
        store=store,
    )
    assert ctx is None
    assert not gate.ok
    assert gate.status == "denied_acl"
    assert comment and "allowlist" in comment


def test_begin_and_finish_ok():
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=42,
        env={
            "YDBDOC_ALLOWED_ACTORS": "sintjuri",
            "GITHUB_ACTOR": "sintjuri",
            "YDBDOC_DAILY_BUDGET_RUB": "5000",
            "YDBDOC_TRANSCRIPT_BACKEND": "memory",
        },
        ledger=ledger,
        store=store,
    )
    assert gate.ok and ctx is not None and comment is None
    ctx.recorder.record(
        role="translate",
        messages=[{"role": "user", "content": "hi"}],
        content="hello",
        model_slug="x",
    )
    finish_ops_job(ctx, status="ok", cost_rub=1.25, input_tokens=10, output_tokens=5)
    assert ledger.sum_cost_for_day(ctx.run_day) == 1.25
    assert store.exists_run(ctx.run_id)
    assert store.get(ctx.run_id, "manifest.json") is not None


def test_expired_continue():
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    ctx, gate, comment = begin_ops_job(
        mode="continue",
        repo="o/r",
        source_pr=7,
        parent_run_id="missing-run",
        env={
            "YDBDOC_ALLOWED_ACTORS": "",
            "GITHUB_ACTOR": "sintjuri",
            "YDBDOC_DAILY_BUDGET_RUB": "5000",
        },
        ledger=ledger,
        store=store,
    )
    assert ctx is None
    assert gate.status == "expired_context"
    assert comment and "14 дней" in comment
