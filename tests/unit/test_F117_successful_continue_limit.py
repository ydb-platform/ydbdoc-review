from ydbdoc_review.ops.lifecycle import begin_ops_job
from ydbdoc_review.ops.runs import InMemoryRunsLedger, RunRecord
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


def _record(*, run_id: str, source_pr: int, mode: str, status: str, index: int = 0) -> RunRecord:
    return RunRecord(
        run_day="2026-09-12",
        run_id=run_id,
        actor="sintjuri",
        mode=mode,
        repo="o/r",
        source_pr=source_pr,
        status=status,
        continue_index=index,
    )


def test_F117_success_statuses() -> None:
    ledger = InMemoryRunsLedger()
    source_pr = 117
    for index, status in enumerate(("ok", "published_red", "ok"), start=1):
        ledger.upsert_run(
            _record(
                run_id=f"success-{index}",
                source_pr=source_pr,
                mode="continue",
                status=status,
                index=index,
            )
        )
    for index, status in enumerate(
        ("denied_acl", "denied_quota", "expired_context", "failed"),
        start=1,
    ):
        ledger.upsert_run(
            _record(
                run_id=f"ignored-{index}",
                source_pr=source_pr,
                mode="continue",
                status=status,
            )
        )

    assert ledger.count_successful_continues(source_pr) == 3


def test_F117_chain_limit() -> None:
    ledger = InMemoryRunsLedger()
    source_pr = 117
    for index in range(1, 4):
        ledger.upsert_run(
            _record(
                run_id=f"continue-{index}",
                source_pr=source_pr,
                mode="continue",
                status="ok",
                index=index,
            )
        )
    # A new translate branch does not reset the source PR's continue budget.
    ledger.upsert_run(
        _record(
            run_id="new-translate",
            source_pr=source_pr,
            mode="translate",
            status="ok",
        )
    )

    ctx, gate, comment = begin_ops_job(
        mode="continue",
        repo="o/r",
        source_pr=source_pr,
        env={
            "YDBDOC_ALLOWED_ACTORS": "sintjuri",
            "GITHUB_ACTOR": "sintjuri",
            "YDBDOC_DAILY_BUDGET_RUB": "5000",
        },
        ledger=ledger,
        store=InMemoryTranscriptStore(),
    )

    assert ctx is None
    assert not gate.ok
    assert gate.reason == "max continues"
    assert comment and "лимит continue исчерпан" in comment
