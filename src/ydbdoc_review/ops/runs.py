"""Run ledger for daily ₽ quota and continue counts."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from ydbdoc_review.ops.msk import msk_today

logger = logging.getLogger(__name__)


@dataclass
class RunRecord:
    run_day: str
    run_id: str
    actor: str
    mode: str  # translate | verify | continue
    repo: str
    source_pr: int
    status: str
    cost_rub: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    continue_index: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    translation_pr: int | None = None
    parent_run_id: str | None = None
    s3_prefix: str | None = None


class RunsLedger(Protocol):
    def sum_cost_for_day(self, run_day: str) -> float: ...

    def count_successful_continues(self, source_pr: int) -> int: ...

    def latest_run_id(
        self,
        source_pr: int,
        *,
        modes: tuple[str, ...] | None = None,
        statuses: tuple[str, ...] = ("ok",),
    ) -> str | None: ...

    def upsert_run(self, record: RunRecord) -> None: ...


class InMemoryRunsLedger:
    """In-process ledger for unit tests and offline dry-runs."""

    def __init__(self) -> None:
        self.records: list[RunRecord] = []

    def sum_cost_for_day(self, run_day: str) -> float:
        return sum(r.cost_rub for r in self.records if r.run_day == run_day)

    def count_successful_continues(self, source_pr: int) -> int:
        return sum(
            1
            for r in self.records
            if r.source_pr == source_pr
            and r.mode == "continue"
            and r.status == "ok"
        )

    def latest_run_id(
        self,
        source_pr: int,
        *,
        modes: tuple[str, ...] | None = None,
        statuses: tuple[str, ...] = ("ok",),
    ) -> str | None:
        candidates = [
            r
            for r in self.records
            if r.source_pr == source_pr
            and r.status in statuses
            and (modes is None or r.mode in modes)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda r: r.started_at, reverse=True)
        return candidates[0].run_id

    def upsert_run(self, record: RunRecord) -> None:
        self.records = [r for r in self.records if r.run_id != record.run_id]
        self.records.append(record)


def new_run_id() -> str:
    return str(uuid.uuid4())


class YdbRunsLedger:
    """YDB-backed ledger (table ``runs``, index ``runs_by_source_pr``)."""

    def __init__(self, driver: object) -> None:
        import ydb

        self._ydb = ydb
        self._driver = driver
        self._pool = ydb.SessionPool(driver)

    def close(self) -> None:
        try:
            self._pool.stop()
        except Exception:
            pass

    def sum_cost_for_day(self, run_day: str) -> float:
        ydb = self._ydb
        query = """
        DECLARE $day AS Utf8;
        SELECT SUM(cost_rub) AS total FROM runs WHERE run_day = $day;
        """

        def _cal(session: object) -> float:
            prep = session.prepare(query)  # type: ignore[attr-defined]
            result = session.transaction().execute(  # type: ignore[attr-defined]
                prep,
                {"$day": run_day},
                commit_tx=True,
            )
            rows = result[0].rows
            if not rows:
                return 0.0
            total = rows[0].total
            return float(total) if total is not None else 0.0

        return float(self._pool.retry_operation_sync(_cal))

    def count_successful_continues(self, source_pr: int) -> int:
        rows = self._fetch_by_source_pr(source_pr)
        return sum(
            1
            for r in rows
            if r.get("mode") == "continue" and r.get("status") == "ok"
        )

    def latest_run_id(
        self,
        source_pr: int,
        *,
        modes: tuple[str, ...] | None = None,
        statuses: tuple[str, ...] = ("ok",),
    ) -> str | None:
        rows = self._fetch_by_source_pr(source_pr)
        filtered = []
        for r in rows:
            if r.get("status") not in statuses:
                continue
            if modes is not None and r.get("mode") not in modes:
                continue
            filtered.append(r)
        if not filtered:
            return None
        filtered.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        rid = filtered[0].get("run_id")
        return str(rid) if rid else None

    def upsert_run(self, record: RunRecord) -> None:
        ydb = self._ydb
        query = """
        DECLARE $run_day AS Utf8;
        DECLARE $run_id AS Utf8;
        DECLARE $started_at AS Timestamp;
        DECLARE $finished_at AS Timestamp?;
        DECLARE $actor AS Utf8;
        DECLARE $mode AS Utf8;
        DECLARE $repo AS Utf8;
        DECLARE $source_pr AS Uint64;
        DECLARE $translation_pr AS Uint64?;
        DECLARE $status AS Utf8;
        DECLARE $cost_rub AS Double;
        DECLARE $input_tokens AS Uint64;
        DECLARE $output_tokens AS Uint64;
        DECLARE $parent_run_id AS Utf8?;
        DECLARE $continue_index AS Uint32;
        DECLARE $s3_prefix AS Utf8?;

        REPLACE INTO runs (
            run_day, run_id, started_at, finished_at, actor, mode, repo,
            source_pr, translation_pr, status, cost_rub, input_tokens,
            output_tokens, parent_run_id, continue_index, s3_prefix
        ) VALUES (
            $run_day, $run_id, $started_at, $finished_at, $actor, $mode, $repo,
            $source_pr, $translation_pr, $status, $cost_rub, $input_tokens,
            $output_tokens, $parent_run_id, $continue_index, $s3_prefix
        );
        """

        def _to_us(dt: datetime | None) -> int | None:
            if dt is None:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1_000_000)

        params = {
            "$run_day": record.run_day,
            "$run_id": record.run_id,
            "$started_at": _to_us(record.started_at),
            "$finished_at": _to_us(record.finished_at),
            "$actor": record.actor,
            "$mode": record.mode,
            "$repo": record.repo,
            "$source_pr": record.source_pr,
            "$translation_pr": record.translation_pr,
            "$status": record.status,
            "$cost_rub": float(record.cost_rub),
            "$input_tokens": int(record.input_tokens),
            "$output_tokens": int(record.output_tokens),
            "$parent_run_id": record.parent_run_id,
            "$continue_index": int(record.continue_index),
            "$s3_prefix": record.s3_prefix,
        }

        def _cal(session: object) -> None:
            prep = session.prepare(query)  # type: ignore[attr-defined]
            session.transaction().execute(prep, params, commit_tx=True)  # type: ignore[attr-defined]

        self._pool.retry_operation_sync(_cal)

    def _fetch_by_source_pr(self, source_pr: int) -> list[dict]:
        query = """
        DECLARE $pr AS Uint64;
        SELECT run_id, mode, status, continue_index, started_at
        FROM runs VIEW runs_by_source_pr
        WHERE source_pr = $pr;
        """

        def _cal(session: object) -> list[dict]:
            prep = session.prepare(query)  # type: ignore[attr-defined]
            result = session.transaction().execute(  # type: ignore[attr-defined]
                prep,
                {"$pr": source_pr},
                commit_tx=True,
            )
            out: list[dict] = []
            for row in result[0].rows:
                out.append(
                    {
                        "run_id": row.run_id,
                        "mode": row.mode,
                        "status": row.status,
                        "continue_index": row.continue_index,
                        "started_at": str(row.started_at),
                    }
                )
            return out

        try:
            return list(self._pool.retry_operation_sync(_cal))
        except Exception as exc:
            logger.warning("YDB fetch by source_pr failed: %s", exc)
            return []


def create_runs_ledger(
    *,
    backend: str = "auto",
    env: dict[str, str] | None = None,
) -> RunsLedger:
    """``memory`` | ``ydb`` | ``auto`` (ydb if SA key present else memory)."""
    import os

    env_map = env or dict(os.environ)
    choice = (backend or "auto").strip().lower()
    if choice == "memory":
        return InMemoryRunsLedger()
    if choice == "ydb" or (
        choice == "auto"
        and (
            env_map.get("YDBDOC_YDB_SA_KEY_FILE")
            or env_map.get("YDB_SA_KEY")
            or env_map.get("SA_KEY_FILE")
        )
    ):
        from ydbdoc_review.ops.ydb_driver import make_ydb_driver

        driver = make_ydb_driver(env=env_map)
        return YdbRunsLedger(driver)
    return InMemoryRunsLedger()
