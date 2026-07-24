"""Job lifecycle: ACL/quota gates, ledger + transcript persistence."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ydbdoc_review.ops.continue_cmd import MAX_CONTINUES_PER_PR
from ydbdoc_review.ops.gates import (
    GateResult,
    acl_deny_comment,
    check_acl,
    check_daily_quota,
    expired_context_comment,
    parse_allowed_actors,
    quota_deny_comment,
    retention_notice,
)
from ydbdoc_review.ops.msk import msk_today
from ydbdoc_review.ops.recorder import LlmTranscriptRecorder
from ydbdoc_review.ops.runs import (
    InMemoryRunsLedger,
    RunRecord,
    RunsLedger,
    create_runs_ledger,
    new_run_id,
)
from ydbdoc_review.ops.transcripts import (
    NullTranscriptStore,
    TranscriptStore,
    create_transcript_store,
)

logger = logging.getLogger(__name__)


@dataclass
class OpsContext:
    actor: str
    run_id: str
    run_day: str
    mode: str
    repo: str
    source_pr: int
    ledger: RunsLedger
    store: TranscriptStore
    recorder: LlmTranscriptRecorder
    budget_rub: float
    parent_run_id: str | None = None
    continue_index: int = 0
    translation_pr: int | None = None
    continue_feedback: str | None = None


def resolve_actor(env: dict[str, str] | None = None) -> str:
    env = env or dict(os.environ)
    return (env.get("GITHUB_ACTOR") or env.get("YDBDOC_ACTOR") or "local").strip()


def _ops_enabled(env: dict[str, str]) -> bool:
    if env.get("YDBDOC_SKIP_OPS_GATES", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return False
    return True


def begin_ops_job(
    *,
    mode: str,
    repo: str,
    source_pr: int,
    translation_pr: int | None = None,
    parent_run_id: str | None = None,
    continue_feedback: str | None = None,
    env: dict[str, str] | None = None,
    ledger: RunsLedger | None = None,
    store: TranscriptStore | None = None,
) -> tuple[OpsContext | None, GateResult, str | None]:
    """Start gates. Returns (ctx|None, gate, deny_comment|None).

    When ops gates are skipped, returns a lightweight in-memory ctx still
    (for optional transcript capture) with ok gate.
    """
    env_map = env or dict(os.environ)
    actor = resolve_actor(env_map)
    budget = float(env_map.get("YDBDOC_DAILY_BUDGET_RUB") or "5000")
    allowed = parse_allowed_actors(env_map.get("YDBDOC_ALLOWED_ACTORS"))
    run_day = msk_today()
    run_id = new_run_id()

    if not _ops_enabled(env_map):
        ctx = OpsContext(
            actor=actor,
            run_id=run_id,
            run_day=run_day,
            mode=mode,
            repo=repo,
            source_pr=source_pr,
            translation_pr=translation_pr,
            ledger=ledger or InMemoryRunsLedger(),
            store=store or NullTranscriptStore(),
            recorder=LlmTranscriptRecorder(),
            budget_rub=budget,
            parent_run_id=parent_run_id,
            continue_feedback=continue_feedback,
        )
        return ctx, GateResult(ok=True), None

    acl = check_acl(actor, allowed)
    if not acl.ok:
        return None, acl, acl_deny_comment(actor)

    try:
        ledger_impl: RunsLedger = ledger or create_runs_ledger(
            backend=env_map.get("YDBDOC_RUNS_LEDGER", "auto"),
            env=env_map,
        )
    except Exception as exc:
        logger.warning("Runs ledger unavailable (%s); continuing without quota", exc)
        ledger_impl = InMemoryRunsLedger()

    spent = ledger_impl.sum_cost_for_day(run_day)
    quota = check_daily_quota(spent_rub=spent, budget_rub=budget)
    if not quota.ok:
        # record denial
        try:
            ledger_impl.upsert_run(
                RunRecord(
                    run_day=run_day,
                    run_id=run_id,
                    actor=actor,
                    mode=mode,
                    repo=repo,
                    source_pr=source_pr,
                    translation_pr=translation_pr,
                    status="denied_quota",
                    cost_rub=0.0,
                    parent_run_id=parent_run_id,
                )
            )
        except Exception as exc:
            logger.warning("Failed to record denied_quota: %s", exc)
        return None, quota, quota_deny_comment(spent_rub=spent, budget_rub=budget)

    continue_index = 0
    if mode == "continue":
        n = ledger_impl.count_successful_continues(source_pr)
        if n >= MAX_CONTINUES_PER_PR:
            msg = (
                f"⛔ **ydbdoc-review:** лимит continue исчерпан "
                f"({MAX_CONTINUES_PER_PR} на PR).\n\n"
                + expired_context_comment(source_pr).split("\n\n", 1)[-1]
            )
            return (
                None,
                GateResult(ok=False, reason="max continues", status="denied_quota"),
                msg,
            )
        continue_index = n + 1
        if parent_run_id is None:
            parent_run_id = ledger_impl.latest_run_id(
                source_pr, modes=("translate", "verify", "continue")
            )

    backend = (env_map.get("YDBDOC_TRANSCRIPT_BACKEND") or "ydb").strip().lower()
    try:
        store_impl: TranscriptStore = store or create_transcript_store(
            backend, env=env_map
        )
    except Exception as exc:
        logger.warning("Transcript store unavailable (%s); using null store", exc)
        store_impl = NullTranscriptStore()

    if mode == "continue":
        if not parent_run_id or not store_impl.exists_run(parent_run_id):
            try:
                ledger_impl.upsert_run(
                    RunRecord(
                        run_day=run_day,
                        run_id=run_id,
                        actor=actor,
                        mode=mode,
                        repo=repo,
                        source_pr=source_pr,
                        translation_pr=translation_pr,
                        status="expired_context",
                        parent_run_id=parent_run_id,
                        continue_index=continue_index,
                    )
                )
            except Exception as exc:
                logger.warning("Failed to record expired_context: %s", exc)
            return (
                None,
                GateResult(
                    ok=False, reason="expired context", status="expired_context"
                ),
                expired_context_comment(source_pr),
            )

    ctx = OpsContext(
        actor=actor,
        run_id=run_id,
        run_day=run_day,
        mode=mode,
        repo=repo,
        source_pr=source_pr,
        translation_pr=translation_pr,
        ledger=ledger_impl,
        store=store_impl,
        recorder=LlmTranscriptRecorder(),
        budget_rub=budget,
        parent_run_id=parent_run_id,
        continue_index=continue_index,
        continue_feedback=continue_feedback,
    )
    return ctx, GateResult(ok=True), None


def finish_ops_job(
    ctx: OpsContext,
    *,
    status: str,
    cost_rub: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    translation_pr: int | None = None,
    report_text: str | None = None,
) -> None:
    """Persist ledger row + flush LLM transcripts + optional report.md."""
    prefix = f"runs/{ctx.source_pr}/{ctx.run_id}/"
    try:
        ctx.recorder.flush_to_store(ctx.store, ctx.run_id)
        if report_text:
            ctx.store.put(ctx.run_id, "report.md", report_text)
        if ctx.continue_feedback:
            ctx.store.put(ctx.run_id, "user/feedback.md", ctx.continue_feedback)
        ctx.store.put(
            ctx.run_id,
            "manifest.json",
            __import__("json").dumps(
                {
                    "run_id": ctx.run_id,
                    "mode": ctx.mode,
                    "source_pr": ctx.source_pr,
                    "parent_run_id": ctx.parent_run_id,
                    "continue_index": ctx.continue_index,
                    "actor": ctx.actor,
                    "status": status,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as exc:
        logger.warning("Failed to flush transcripts: %s", exc)

    try:
        ctx.ledger.upsert_run(
            RunRecord(
                run_day=ctx.run_day,
                run_id=ctx.run_id,
                actor=ctx.actor,
                mode=ctx.mode,
                repo=ctx.repo,
                source_pr=ctx.source_pr,
                translation_pr=translation_pr or ctx.translation_pr,
                status=status,
                cost_rub=cost_rub,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                parent_run_id=ctx.parent_run_id,
                continue_index=ctx.continue_index,
                s3_prefix=prefix,
                finished_at=datetime.now(timezone.utc),
            )
        )
    except Exception as exc:
        logger.warning("Failed to upsert run ledger: %s", exc)


def append_retention_footer(body: str) -> str:
    notice = retention_notice()
    if notice in body:
        return body
    return body.rstrip() + "\n\n" + notice + "\n"
