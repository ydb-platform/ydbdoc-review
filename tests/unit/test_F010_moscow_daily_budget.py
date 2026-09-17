"""F-010: shared budget is keyed by Moscow calendar day."""

from datetime import datetime
from zoneinfo import ZoneInfo

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.ops.msk import msk_today
from ydbdoc_review.ops.runs import InMemoryRunsLedger, RunRecord


def _record(*, run_id: str, mode: str, run_day: str, cost_rub: float) -> RunRecord:
    return RunRecord(
        run_day=run_day,
        run_id=run_id,
        actor="runner",
        mode=mode,
        repo="ydb-platform/ydb",
        source_pr=1,
        status="ok",
        cost_rub=cost_rub,
    )


def test_F010_default_5000_without_env(monkeypatch):
    monkeypatch.delenv("YDBDOC_DAILY_BUDGET_RUB", raising=False)

    config = load_config(env={})

    assert config.ops.daily_budget_rub == 5000.0


def test_F010_sum_three_modes_for_moscow_date():
    ledger = InMemoryRunsLedger()
    ledger.records = [
        _record(run_id="translate", mode="translate", run_day="2026-09-10", cost_rub=1200),
        _record(run_id="verify", mode="verify", run_day="2026-09-10", cost_rub=800),
        _record(run_id="continue", mode="continue", run_day="2026-09-10", cost_rub=300),
        _record(run_id="previous", mode="translate", run_day="2026-09-09", cost_rub=9999),
    ]

    assert ledger.sum_cost_for_day("2026-09-10") == 2300


def test_F010_moscow_midnight_with_other_runner_tz(monkeypatch):
    moscow = ZoneInfo("Europe/Moscow")
    before_midnight = datetime(2026, 9, 10, 23, 59, tzinfo=moscow)
    after_midnight = datetime(2026, 9, 11, 0, 0, tzinfo=moscow)
    monkeypatch.setattr("ydbdoc_review.ops.msk.msk_now", lambda: before_midnight)
    previous_day = msk_today()
    monkeypatch.setattr("ydbdoc_review.ops.msk.msk_now", lambda: after_midnight)
    current_day = msk_today()

    ledger = InMemoryRunsLedger()
    ledger.records = [
        _record(run_id="previous", mode="translate", run_day=previous_day, cost_rub=100),
        _record(run_id="current", mode="verify", run_day=current_day, cost_rub=200),
    ]

    assert (previous_day, current_day) == ("2026-09-10", "2026-09-11")
    assert ledger.sum_cost_for_day(current_day) == 200
