"""F-009: authorization fails closed before paid work or mutations."""

from __future__ import annotations

from ydbdoc_review.ops.lifecycle import begin_ops_job


class MustNotBeTouched:
    def sum_cost_for_day(self, _run_day: str) -> float:
        raise AssertionError("ledger must not be read after ACL denial")

    def upsert_run(self, _record: object) -> None:
        raise AssertionError("ledger must not be mutated after ACL denial")


def test_F009_empty_allowlist_denies_before_ledger_or_llm():
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=9,
        env={
            "YDBDOC_ALLOWED_ACTORS": "",
            "GITHUB_ACTOR": "trusted-looking-actor",
            "YDBDOC_DAILY_BUDGET_RUB": "100",
        },
        ledger=MustNotBeTouched(),
    )

    assert ctx is None
    assert not gate.ok
    assert gate.status == "denied_acl"
    assert comment and "allowlist" in comment


def test_F009_missing_actor_denies_before_ledger_or_llm():
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=9,
        env={
            "YDBDOC_ALLOWED_ACTORS": "trusted-operator",
            "YDBDOC_DAILY_BUDGET_RUB": "100",
        },
        ledger=MustNotBeTouched(),
    )

    assert ctx is None
    assert not gate.ok
    assert gate.status == "denied_acl"
    assert comment and "unknown" in comment
