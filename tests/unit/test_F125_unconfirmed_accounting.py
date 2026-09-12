import json
import logging

from ydbdoc_review.ops.lifecycle import begin_ops_job, finish_ops_job
from ydbdoc_review.ops.runs import InMemoryRunsLedger, RunRecord
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


class FlakySaveLedger(InMemoryRunsLedger):
    def __init__(self) -> None:
        super().__init__()
        self.fail_writes = False

    def upsert_run(self, record: RunRecord) -> None:
        if self.fail_writes:
            raise RuntimeError("ledger write failed")
        super().upsert_run(record)


def _env() -> dict[str, str]:
    return {
        "GITHUB_ACTOR": "worker",
        "YDBDOC_ALLOWED_ACTORS": "worker",
        "YDBDOC_DAILY_BUDGET_RUB": "100",
        "YDBDOC_TRANSCRIPT_BACKEND": "memory",
    }


def test_F125_failed_save(caplog) -> None:
    ledger = FlakySaveLedger()
    store = InMemoryTranscriptStore()
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="ydb-platform/ydb",
        source_pr=125,
        env=_env(),
        ledger=ledger,
        store=store,
        idempotency_key="failed-save",
    )

    assert ctx is not None and gate.ok and comment is None
    ledger.fail_writes = True
    with caplog.at_level(logging.WARNING):
        finish_ops_job(ctx, status="ok", cost_rub=12.5, translation_pr=900)

    pending = store.get("__accounting__", f"unconfirmed/{ctx.run_id}.json")
    assert pending is not None
    payload = json.loads(pending)
    assert payload["record"]["cost_rub"] == 12.5
    assert payload["record"]["translation_pr"] == 900
    assert "unconfirmed" in caplog.text


def test_F125_recover_once() -> None:
    ledger = FlakySaveLedger()
    store = InMemoryTranscriptStore()
    ctx, _, _ = begin_ops_job(
        mode="translate",
        repo="ydb-platform/ydb",
        source_pr=125,
        env=_env(),
        ledger=ledger,
        store=store,
        idempotency_key="recover-source",
    )
    assert ctx is not None
    ledger.fail_writes = True
    finish_ops_job(ctx, status="ok", cost_rub=12.5)

    denied_ctx, denied_gate, _ = begin_ops_job(
        mode="translate",
        repo="ydb-platform/ydb",
        source_pr=125,
        env=_env(),
        ledger=ledger,
        store=store,
        idempotency_key="blocked-until-recovery",
    )
    assert denied_ctx is None
    assert denied_gate.status == "denied_accounting"

    ledger.fail_writes = False
    recovered_ctx, recovered_gate, _ = begin_ops_job(
        mode="translate",
        repo="ydb-platform/ydb",
        source_pr=125,
        env=_env(),
        ledger=ledger,
        store=store,
        idempotency_key="after-recovery",
    )
    assert recovered_ctx is not None and recovered_gate.ok
    assert ledger.sum_cost_for_day(recovered_ctx.run_day) == 12.5

    finish_ops_job(recovered_ctx, status="ok", cost_rub=1.0)
    assert ledger.sum_cost_for_day(recovered_ctx.run_day) == 13.5
