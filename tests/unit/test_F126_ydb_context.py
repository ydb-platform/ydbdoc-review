import json

from ydbdoc_review.ops.lifecycle import begin_ops_job, finish_ops_job, load_parent_run_context
from ydbdoc_review.ops.runs import InMemoryRunsLedger
from ydbdoc_review.ops.transcripts import InMemoryTranscriptStore


def _env() -> dict[str, str]:
    return {
        "GITHUB_ACTOR": "worker",
        "YDBDOC_ALLOWED_ACTORS": "worker",
        "YDBDOC_DAILY_BUDGET_RUB": "100",
        "YDBDOC_TRANSCRIPT_BACKEND": "memory",
    }


def test_F126_context_payload() -> None:
    store = InMemoryTranscriptStore()
    ctx, gate, _ = begin_ops_job(
        mode="translate",
        repo="ydb-platform/ydb",
        source_pr=126,
        env=_env(),
        ledger=InMemoryRunsLedger(),
        store=store,
        idempotency_key="context-payload",
    )
    assert ctx is not None and gate.ok
    ctx.recorder.record(
        role="analyze",
        messages=[{"role": "user", "content": "translate docs"}],
        content="pair docs/a.md -> docs/a.en.md; anchor #intro",
        model_slug="test-model",
    )
    payload = {
        "instructions": ["translate docs"],
        "responses": ["pair docs/a.md -> docs/a.en.md"],
        "files": [{"source": "docs/a.md", "ref": "sha:a"}],
        "pairs": [{"source": "docs/a.md", "target": "docs/a.en.md"}],
        "anchors": ["intro"],
        "redirects": [{"from": "old", "to": "new"}],
        "questions": ["keep redirect?"],
        "qa": {"status": "passed"},
        "analyze": {"decision": "translate", "reason": "source changed"},
        "target_version": "v0.1.0",
        "evidence": {"F-143": "cost estimate attached"},
        "api_token": "must-not-persist",
    }
    finish_ops_job(ctx, status="ok", cost_rub=1.0, context_payload=payload)

    raw = store.get(ctx.run_id, "context/context.json")
    assert raw is not None
    saved = json.loads(raw)
    assert saved["files"] == payload["files"]
    assert saved["pairs"] == payload["pairs"]
    assert saved["qa"] == payload["qa"]
    assert saved["analyze"] == payload["analyze"]
    assert saved["target_version"] == "v0.1.0"
    assert "api_token" not in saved
    assert "must-not-persist" not in raw.decode()


def test_F126_early_cases() -> None:
    store = InMemoryTranscriptStore()
    ctx, gate, _ = begin_ops_job(
        mode="translate",
        repo="ydb-platform/ydb",
        source_pr=126,
        env=_env(),
        ledger=InMemoryRunsLedger(),
        store=store,
        idempotency_key="unsupported-files",
    )
    assert ctx is not None and gate.ok
    finish_ops_job(
        ctx,
        status="noop",
        cost_rub=0.0,
        context_payload={"scope": [], "reason": "no supported files", "analyze": None},
    )
    raw = store.get(ctx.run_id, "context/context.json")
    assert raw is not None
    saved = json.loads(raw)
    assert saved == {"scope": [], "reason": "no supported files", "analyze": None}
    assert load_parent_run_context(
        type("ContinueContext", (), {"parent_run_id": ctx.run_id, "store": store})()
    )
