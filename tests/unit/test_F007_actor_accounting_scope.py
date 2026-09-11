"""F-007: paid entries share actor and accounting across launch providers."""

from ydbdoc_review.ops.lifecycle import begin_ops_job, finish_ops_job
from ydbdoc_review.ops.runs import InMemoryRunsLedger, RunRecord
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


def test_F007_provider_switch_keeps_actor_budget_and_ignores_diagnostics() -> None:
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    common = {
        "GITHUB_ACTOR": "worker",
        "YDBDOC_DAILY_BUDGET_RUB": "10",
        "YDBDOC_TRANSCRIPT_BACKEND": "memory",
    }

    translate, gate, _ = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=7,
        env={**common, "YDBDOC_LLM_PROVIDER": "yandex_cloud"},
        ledger=ledger,
        store=store,
    )
    assert gate.ok and translate is not None
    finish_ops_job(translate, status="ok", cost_rub=6.0)

    verify, gate, _ = begin_ops_job(
        mode="verify",
        repo="o/r",
        source_pr=7,
        env={**common, "YDBDOC_LLM_PROVIDER": "eliza"},
        ledger=ledger,
        store=store,
    )
    assert verify is not None
    assert verify.actor == translate.actor == "worker"
    finish_ops_job(verify, status="ok", cost_rub=4.0)

    denied, gate, _ = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=8,
        env={**common, "YDBDOC_LLM_PROVIDER": "yandex_cloud"},
        ledger=ledger,
        store=store,
    )
    assert denied is None
    assert gate.status == "denied_quota"

    ledger.upsert_run(
        RunRecord(
            run_day=translate.run_day,
            run_id="diagnostic",
            actor="worker",
            mode="diagnostic",
            repo="o/r",
            source_pr=7,
            status="diagnostic",
        )
    )
    assert ledger.count_successful_continues(7) == 0
    assert ledger.latest_run_id(7, modes=("translate", "verify", "continue")) == verify.run_id
