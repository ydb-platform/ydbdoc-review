from ydbdoc_review.ops.gates import expired_context_comment, store_unavailable_comment
from ydbdoc_review.ops.lifecycle import begin_ops_job
from ydbdoc_review.ops.runs import InMemoryRunsLedger
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


def test_F119_missing_expired() -> None:
    ledger = InMemoryRunsLedger()
    ctx, gate, comment = begin_ops_job(
        mode="continue",
        repo="o/r",
        source_pr=119,
        parent_run_id="missing-run",
        env={
            "YDBDOC_ALLOWED_ACTORS": "sintjuri",
            "GITHUB_ACTOR": "sintjuri",
            "YDBDOC_DAILY_BUDGET_RUB": "5000",
        },
        ledger=ledger,
        store=InMemoryTranscriptStore(),
    )

    assert ctx is None
    assert gate.status == "expired_context"
    assert comment == expired_context_comment(119)
    assert "doc_translate" in comment
    assert "doc_verify" in comment
    assert "Удалить ветку" not in comment


def test_F119_store_failure() -> None:
    message = store_unavailable_comment(119, detail="backend unavailable")

    assert "не" in message
    assert "TTL" in message
    assert "14 дней" not in message
