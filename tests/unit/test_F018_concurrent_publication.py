"""F-018: repeated events and superseded runs are safe to publish."""

from ydbdoc_review.ops.lifecycle import begin_ops_job, finish_ops_job
from ydbdoc_review.ops.runs import InMemoryRunsLedger
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


def _env() -> dict[str, str]:
    return {
        "GITHUB_ACTOR": "worker",
        "YDBDOC_ALLOWED_ACTORS": "worker",
        "YDBDOC_DAILY_BUDGET_RUB": "1000",
        "YDBDOC_TRANSCRIPT_BACKEND": "memory",
    }


def test_F018_duplicate_event() -> None:
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    kwargs = {
        "mode": "translate",
        "repo": "o/r",
        "source_pr": 18,
        "env": _env(),
        "ledger": ledger,
        "store": store,
        "idempotency_key": "pull_request:18:head:abc123",
    }

    first, first_gate, _ = begin_ops_job(**kwargs)
    duplicate, duplicate_gate, duplicate_comment = begin_ops_job(**kwargs)

    assert first_gate.ok and first is not None
    assert duplicate is None
    assert duplicate_gate.status == "duplicate_event"
    assert duplicate_comment is None

    finish_ops_job(first, status="ok", cost_rub=12.5, translation_pr=118)
    assert len(ledger.records) == 1
    assert ledger.sum_cost_for_day(first.run_day) == 12.5


def test_F018_superseded() -> None:
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    first, first_gate, _ = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=18,
        env=_env(),
        ledger=ledger,
        store=store,
        idempotency_key="pull_request:18:head:old",
    )
    second, second_gate, _ = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=18,
        env=_env(),
        ledger=ledger,
        store=store,
        idempotency_key="pull_request:18:head:new",
    )

    assert first_gate.ok and first is not None
    assert second_gate.ok and second is not None

    finish_ops_job(first, status="superseded", cost_rub=7.0, translation_pr=117)
    finish_ops_job(second, status="ok", cost_rub=9.0, translation_pr=118)

    by_run = {record.run_id: record for record in ledger.records}
    assert by_run[first.run_id].status == "superseded"
    assert by_run[first.run_id].cost_rub == 7.0
    assert by_run[second.run_id].status == "ok"
    assert ledger.sum_cost_for_day(first.run_day) == 16.0
    assert ledger.latest_run_id(18, repo="o/r") == second.run_id
