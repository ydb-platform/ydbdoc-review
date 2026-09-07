"""YDB run-ledger selector contracts without an external YDB service."""

from ydbdoc_review.ops.runs import YdbRunsLedger


def _ledger() -> YdbRunsLedger:
    ledger = object.__new__(YdbRunsLedger)
    ledger._fetch_by_source_pr = lambda _source_pr: [  # type: ignore[method-assign]
        {
            "run_id": "same-ok",
            "mode": "translate",
            "repo": "o/r",
            "status": "ok",
            "continue_index": 0,
            "started_at": "2026-09-07T10:00:00Z",
        },
        {
            "run_id": "same-failed",
            "mode": "continue",
            "repo": "o/r",
            "status": "failed",
            "continue_index": 1,
            "started_at": "2026-09-07T11:00:00Z",
        },
        {
            "run_id": "foreign-newest",
            "mode": "translate",
            "repo": "other/r",
            "status": "ok",
            "continue_index": 0,
            "started_at": "2026-09-07T12:00:00Z",
        },
    ]
    return ledger


def test_ydb_latest_run_selector_filters_repo_status_and_exclusions() -> None:
    ledger = _ledger()

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
