"""F-013: quota denials are actionable and never promise auto-retry."""

from __future__ import annotations

from ydbdoc_review.ops.gates import quota_deny_comment
from ydbdoc_review.ops.lifecycle import begin_ops_job
from ydbdoc_review.ops.runs import InMemoryRunsLedger


class SpentLedger(InMemoryRunsLedger):
    def sum_cost_for_day(self, _day: str) -> float:
        return 100.0


def test_F013_denial_contains_amount_limit_and_moscow_date():
    ledger = SpentLedger()

    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=13,
        env={
            "GITHUB_ACTOR": "worker",
            "YDBDOC_ALLOWED_ACTORS": "worker",
            "YDBDOC_DAILY_BUDGET_RUB": "100",
        },
        ledger=ledger,
    )

    assert ctx is None
    assert not gate.ok
    assert gate.status == "denied_quota"
    assert comment is not None
    assert "100.00" in comment
    assert "дата MSK" in comment
    assert "doc_translate" in comment
    assert "Автоматический повтор" in comment


def test_F013_retry_mode_names_manual_command():
    for mode, command in (
        ("translate", "doc_translate"),
        ("verify", "doc_verify"),
        ("continue", "doc_continue"),
    ):
        comment = quota_deny_comment(
            spent_rub=101,
            budget_rub=100,
            run_day="2026-09-11",
            mode=mode,
        )
        assert command in comment
        assert "автоматический" in comment.casefold()
