"""In-memory runs ledger tests."""

from datetime import UTC, datetime

from ydbdoc_review.ops.runs import InMemoryRunsLedger, RunRecord, YdbRunsLedger


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
            started_at=datetime(2026, 7, 22, 10, 0, tzinfo=UTC),
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
            started_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
        )
    )
    assert ledger.sum_cost_for_day("2026-07-22") == 13.5
    assert ledger.count_successful_continues(1) == 1
    assert ledger.latest_run_id(1) == "b"


def test_memory_ledger_treats_published_red_as_successful_publication():
    ledger = InMemoryRunsLedger()
    ledger.records = [
        RunRecord(
            run_day="2026-09-04",
            run_id="translate-red",
            actor="u",
            mode="translate",
            repo="o/r",
            source_pr=7,
            status="published_red",
            started_at=datetime(2026, 9, 4, 10, 0, tzinfo=UTC),
        ),
        RunRecord(
            run_day="2026-09-04",
            run_id="continue-red-1",
            actor="u",
            mode="continue",
            repo="o/r",
            source_pr=7,
            status="published_red",
            continue_index=1,
            started_at=datetime(2026, 9, 4, 11, 0, tzinfo=UTC),
        ),
        RunRecord(
            run_day="2026-09-04",
            run_id="continue-red-2",
            actor="u",
            mode="continue",
            repo="o/r",
            source_pr=7,
            status="published_red",
            continue_index=2,
            started_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        ),
        RunRecord(
            run_day="2026-09-04",
            run_id="continue-red-3",
            actor="u",
            mode="continue",
            repo="o/r",
            source_pr=7,
            status="published_red",
            continue_index=3,
            started_at=datetime(2026, 9, 4, 13, 0, tzinfo=UTC),
        ),
    ]

    assert ledger.count_successful_continues(7) == 3
    assert ledger.latest_run_id(7) == "continue-red-3"


def test_memory_latest_run_selector_filters_repo_status_and_exclusions():
    ledger = InMemoryRunsLedger()
    ledger.records = [
        RunRecord(
            run_day="2026-09-07",
            run_id="same-ok",
            actor="u",
            mode="translate",
            repo="o/r",
            source_pr=7,
            status="ok",
            started_at=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
        ),
        RunRecord(
            run_day="2026-09-07",
            run_id="same-failed",
            actor="u",
            mode="continue",
            repo="o/r",
            source_pr=7,
            status="failed",
            started_at=datetime(2026, 9, 7, 11, 0, tzinfo=UTC),
        ),
        RunRecord(
            run_day="2026-09-07",
            run_id="foreign-newest",
            actor="u",
            mode="translate",
            repo="other/r",
            source_pr=7,
            status="ok",
            started_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        ),
    ]

    assert ledger.latest_run_id(7) == "foreign-newest"
    assert ledger.latest_run_id(7, repo="o/r") == "same-ok"
    assert (
        ledger.latest_run_id(
            7,
            repo="o/r",
            statuses=("ok", "published_red", "failed"),
            run_id="same-failed",
        )
        == "same-failed"
    )
    assert (
        ledger.latest_run_id(
            7,
            repo="o/r",
            modes=("translate", "continue"),
            statuses=("ok", "published_red", "failed"),
        )
        == "same-failed"
    )
    assert (
        ledger.latest_run_id(
            7,
            repo="o/r",
            statuses=("ok", "published_red", "failed"),
            exclude_run_ids=("same-failed",),
        )
        == "same-ok"
    )
    assert (
        ledger.latest_run_id(
            7,
            repo="o/r",
            statuses=("ok", "published_red", "failed"),
            exclude_run_ids=("same-failed", "same-ok"),
        )
        is None
    )
    assert ledger.count_successful_continues(7) == 0


def test_ydb_ledger_treats_published_red_as_successful_publication():
    ledger = object.__new__(YdbRunsLedger)
    ledger._fetch_by_source_pr = lambda _source_pr: [  # type: ignore[method-assign]
        {
            "run_id": "translate-red",
            "mode": "translate",
            "status": "published_red",
            "continue_index": 0,
            "started_at": "2026-09-04T10:00:00Z",
        },
        {
            "run_id": "continue-red-1",
            "mode": "continue",
            "status": "published_red",
            "continue_index": 1,
            "started_at": "2026-09-04T11:00:00Z",
        },
        {
            "run_id": "continue-red-2",
            "mode": "continue",
            "status": "published_red",
            "continue_index": 2,
            "started_at": "2026-09-04T12:00:00Z",
        },
        {
            "run_id": "continue-red-3",
            "mode": "continue",
            "status": "published_red",
            "continue_index": 3,
            "started_at": "2026-09-04T13:00:00Z",
        },
    ]

    assert ledger.count_successful_continues(7) == 3
    assert ledger.latest_run_id(7) == "continue-red-3"
