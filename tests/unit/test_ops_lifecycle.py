"""Lifecycle begin/finish with in-memory backends."""

from ydbdoc_review.ops.lifecycle import (
    begin_ops_job,
    compose_continue_feedback,
    finish_ops_job,
    load_parent_run_context,
)
from ydbdoc_review.ops.runs import InMemoryRunsLedger, RunRecord
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


def test_begin_acl_deny():
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=1,
        env={
            "YDBDOC_ALLOWED_ACTORS": "sintjuri",
            "GITHUB_ACTOR": "other",
            "YDBDOC_DAILY_BUDGET_RUB": "5000",
        },
        ledger=ledger,
        store=store,
    )
    assert ctx is None
    assert not gate.ok
    assert gate.status == "denied_acl"
    assert comment and "allowlist" in comment


def test_begin_and_finish_ok():
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    ctx, gate, comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=42,
        env={
            "YDBDOC_ALLOWED_ACTORS": "sintjuri",
            "GITHUB_ACTOR": "sintjuri",
            "YDBDOC_DAILY_BUDGET_RUB": "5000",
            "YDBDOC_TRANSCRIPT_BACKEND": "memory",
        },
        ledger=ledger,
        store=store,
    )
    assert gate.ok and ctx is not None and comment is None
    ctx.recorder.record(
        role="translate",
        messages=[{"role": "user", "content": "hi"}],
        content="hello",
        model_slug="x",
    )
    finish_ops_job(ctx, status="ok", cost_rub=1.25, input_tokens=10, output_tokens=5)
    assert ledger.sum_cost_for_day(ctx.run_day) == 1.25
    assert store.exists_run(ctx.run_id)
    assert store.get(ctx.run_id, "manifest.json") is not None


def test_published_red_is_selected_as_doc_continue_parent():
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    env = {
        "YDBDOC_ALLOWED_ACTORS": "",
        "GITHUB_ACTOR": "sintjuri",
        "YDBDOC_DAILY_BUDGET_RUB": "5000",
    }
    parent, gate, _comment = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=7,
        env=env,
        ledger=ledger,
        store=store,
    )
    assert gate.ok and parent is not None
    finish_ops_job(parent, status="published_red", cost_rub=1.0)

    child, gate, comment = begin_ops_job(
        mode="continue",
        repo="o/r",
        source_pr=7,
        env=env,
        ledger=ledger,
        store=store,
    )

    assert gate.ok and comment is None and child is not None
    assert child.parent_run_id == parent.run_id


def test_three_published_red_continues_exhaust_limit():
    ledger = InMemoryRunsLedger()
    for index in range(1, 4):
        ledger.upsert_run(
            RunRecord(
                run_day="2026-09-04",
                run_id=f"continue-red-{index}",
                actor="u",
                mode="continue",
                repo="o/r",
                source_pr=7,
                status="published_red",
                continue_index=index,
            )
        )

    ctx, gate, comment = begin_ops_job(
        mode="continue",
        repo="o/r",
        source_pr=7,
        env={
            "YDBDOC_ALLOWED_ACTORS": "",
            "GITHUB_ACTOR": "sintjuri",
            "YDBDOC_DAILY_BUDGET_RUB": "5000",
        },
        ledger=ledger,
        store=InMemoryTranscriptStore(),
    )

    assert ctx is None
    assert gate.status == "denied_quota"
    assert comment and "лимит continue исчерпан" in comment


def test_expired_continue():
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    ctx, gate, comment = begin_ops_job(
        mode="continue",
        repo="o/r",
        source_pr=7,
        parent_run_id="missing-run",
        env={
            "YDBDOC_ALLOWED_ACTORS": "",
            "GITHUB_ACTOR": "sintjuri",
            "YDBDOC_DAILY_BUDGET_RUB": "5000",
        },
        ledger=ledger,
        store=store,
    )
    assert ctx is None
    assert gate.status == "expired_context"
    assert comment and "14 дней" in comment


def test_continue_store_unavailable_is_not_ttl_message():
    """Missing YDB_SA_KEY must not claim 14-day TTL deletion (§6.143)."""
    ledger = InMemoryRunsLedger()
    ctx, gate, comment = begin_ops_job(
        mode="continue",
        repo="o/r",
        source_pr=48047,
        parent_run_id="any",
        env={
            "YDBDOC_ALLOWED_ACTORS": "",
            "GITHUB_ACTOR": "sintjuri",
            "YDBDOC_DAILY_BUDGET_RUB": "5000",
            "YDBDOC_TRANSCRIPT_BACKEND": "ydb",
            # no YDB_SA_KEY → create_transcript_store fails → null store
        },
        ledger=ledger,
        store=None,
    )
    assert ctx is None
    assert gate.status == "expired_context"
    assert comment is not None
    assert "не" in comment and "TTL" in comment
    assert "хранилищ" in comment
    assert "14 дней" not in comment


def test_continue_loads_parent_transcript_into_prompt_context():
    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    parent, gate, _ = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=40385,
        env={"YDBDOC_SKIP_OPS_GATES": "1"},
        ledger=ledger,
        store=store,
    )
    assert gate.ok and parent is not None
    parent.recorder.record(
        role="critic",
        messages=[{"role": "user", "content": "Check security/index.md hierarchy"}],
        content="Device authentication must be nested under authentication.",
        model_slug="critic",
    )
    parent.continue_feedback = "Keep semantic link ownership"
    finish_ops_job(parent, status="ok", cost_rub=1.0)

    child = type(parent)(
        **{
            **parent.__dict__,
            "run_id": "child",
            "mode": "continue",
            "parent_run_id": parent.run_id,
        }
    )
    context = load_parent_run_context(child)
    prompt_feedback = compose_continue_feedback(
        "Translate files that are still missing", context
    )

    assert "Translate files that are still missing" in prompt_feedback
    assert "Keep semantic link ownership" in prompt_feedback
    assert "Check security/index.md hierarchy" in prompt_feedback
    assert "Device authentication must be nested" in prompt_feedback
    assert "historical reference" in prompt_feedback
