"""In-memory runs ledger tests."""

from datetime import datetime, timezone

from ydbdoc_review.ops.runs import InMemoryRunsLedger, RunRecord


def test_sum_and_continues():
    ledger = InMemoryRunsLedger()
    ledger.upsert_run(
        RunRecord(
            run_day="2026-07-22",
            run_id="a",
            actor="u",
            mode="translate",
            repo="o/r",
            source_pr=1,
            status="ok",
            cost_rub=10.5,
            started_at=datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
        )
    )
    ledger.upsert_run(
        RunRecord(
            run_day="2026-07-22",
            run_id="b",
            actor="u",
            mode="continue",
            repo="o/r",
            source_pr=1,
            status="ok",
            cost_rub=3.0,
            continue_index=1,
            started_at=datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
        )
    )
    assert ledger.sum_cost_for_day("2026-07-22") == 13.5
    assert ledger.count_successful_continues(1) == 1
    assert ledger.latest_run_id(1) == "b"
