from datetime import UTC, datetime

from ydbdoc_review.ops.gates import TRANSCRIPT_RETENTION
from ydbdoc_review.ops.runs import InMemoryRunsLedger, RunRecord


def test_F124_record_fields() -> None:
    started = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
    record = RunRecord(
        run_day="2026-09-13",
        run_id="run-124",
        actor="sintjuri",
        mode="continue",
        repo="o/r",
        source_pr=124,
        translation_pr=900,
        status="published_red",
        cost_rub=1.5,
        input_tokens=100,
        output_tokens=50,
        parent_run_id="parent-123",
        continue_index=2,
        started_at=started,
        finished_at=started,
        s3_prefix="runs/124/run-124/",
    )

    assert record.actor == "sintjuri"
    assert (record.mode, record.repo, record.source_pr, record.translation_pr) == (
        "continue",
        "o/r",
        124,
        900,
    )
    assert record.started_at == started
    assert (record.status, record.cost_rub, record.parent_run_id) == (
        "published_red",
        1.5,
        "parent-123",
    )


def test_F124_denial_ttl() -> None:
    ledger = InMemoryRunsLedger()
    denial = RunRecord(
        run_day="2026-09-13",
        run_id="denied",
        actor="sintjuri",
        mode="continue",
        repo="o/r",
        source_pr=124,
        status="denied_quota",
    )
    ledger.upsert_run(denial)

    assert denial.cost_rub == 0.0
    assert denial.input_tokens == denial.output_tokens == 0
    assert ledger.sum_cost_for_day("2026-09-13") == 0.0
    assert TRANSCRIPT_RETENTION.days == 14
