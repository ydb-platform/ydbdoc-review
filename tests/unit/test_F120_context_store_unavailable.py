from ydbdoc_review.ops.gates import store_unavailable_comment
from ydbdoc_review.ops.lifecycle import begin_ops_job, finish_ops_job
from ydbdoc_review.ops.runs import InMemoryRunsLedger
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


class FailingTranscriptStore(InMemoryTranscriptStore):
    def put(self, run_id: str, object_key: str, data: bytes | str) -> None:
        raise RuntimeError("backend unavailable")


def test_F120_continue_denied() -> None:
    ledger = InMemoryRunsLedger()
    ctx, gate, comment = begin_ops_job(
        mode="continue",
        repo="o/r",
        source_pr=120,
        parent_run_id="parent",
        env={
            "YDBDOC_ALLOWED_ACTORS": "sintjuri",
            "GITHUB_ACTOR": "sintjuri",
            "YDBDOC_DAILY_BUDGET_RUB": "5000",
        },
        ledger=ledger,
        store=None,
    )

    assert ctx is None
    assert gate.status == "expired_context"
    assert comment is not None
    assert "хранилище" in comment
    assert "Повторить" in comment or "заново" in comment
    assert "14 дней" not in comment


def test_F120_save_failure() -> None:
    ledger = InMemoryRunsLedger()
    store = FailingTranscriptStore()
    ctx, gate, _ = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=120,
        env={"YDBDOC_SKIP_OPS_GATES": "1"},
        ledger=ledger,
        store=store,
    )
    assert gate.ok and ctx is not None

    # A late context failure must not discard the already completed result.
    finish_ops_job(ctx, status="ok", cost_rub=0.0)
    assert ledger.records[0].status == "ok"
    assert "хранилище" in store_unavailable_comment(120)
    assert "TTL" in store_unavailable_comment(120)
