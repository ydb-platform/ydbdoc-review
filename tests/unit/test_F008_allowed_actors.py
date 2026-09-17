from ydbdoc_review.ops.lifecycle import begin_ops_job, finish_ops_job
from ydbdoc_review.ops.runs import InMemoryRunsLedger
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


def test_F008_all_modes_use_case_insensitive_initiator_allowlist():
    allowed_env = {
        "YDBDOC_ALLOWED_ACTORS": "  trusted-operator , reviewer  ",
        "GITHUB_ACTOR": "TrUsTeD-OpErAtOr",
        "GITHUB_PR_AUTHOR": "different-author",
        "GITHUB_TOKEN_OWNER": "different-owner",
        "YDBDOC_DAILY_BUDGET_RUB": "5000",
        "YDBDOC_TRANSCRIPT_BACKEND": "memory",
    }

    for mode in ("translate", "verify"):
        ctx, gate, comment = begin_ops_job(
            mode=mode,
            repo="o/r",
            source_pr=8,
            env=allowed_env,
            ledger=InMemoryRunsLedger(),
            store=InMemoryTranscriptStore(),
        )
        assert ctx is not None
        assert gate.ok
        assert comment is None

    ledger = InMemoryRunsLedger()
    store = InMemoryTranscriptStore()
    parent, gate, _ = begin_ops_job(
        mode="translate",
        repo="o/r",
        source_pr=8,
        env=allowed_env,
        ledger=ledger,
        store=store,
    )
    assert parent is not None and gate.ok
    finish_ops_job(parent, status="ok", cost_rub=0.0)

    continued, gate, comment = begin_ops_job(
        mode="continue",
        repo="o/r",
        source_pr=8,
        parent_run_id=parent.run_id,
        env=allowed_env,
        ledger=ledger,
        store=store,
    )
    assert continued is not None
    assert gate.ok
    assert comment is None

    denied_env = {
        **allowed_env,
        "GITHUB_ACTOR": "outsider",
    }
    denied, gate, comment = begin_ops_job(
        mode="verify",
        repo="o/r",
        source_pr=8,
        env=denied_env,
        ledger=InMemoryRunsLedger(),
        store=InMemoryTranscriptStore(),
    )
    assert denied is None
    assert not gate.ok
    assert gate.status == "denied_acl"
    assert comment and "outsider" in comment
