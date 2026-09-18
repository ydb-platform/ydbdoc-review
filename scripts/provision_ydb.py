"""Explicit operator provisioning. Never called by product CLI or CI.

Stop all old/new writers before migration. Old tables are renamed, never dropped.
A failed step leaves originals/staging for inspection; no destructive retry/cleanup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import ydb

from ydbdoc_review.ops.ydb_driver import make_ydb_driver
from ydbdoc_review.store import encode, schema


def financial_row(old):
    """Preserve legacy total as one explicitly unsplit RUB ledger entry."""
    day = str(old["run_day"])
    datetime.strptime(day, "%Y-%m-%d")
    raw = old.get("cost_rub")
    cost = None if raw is None else Decimal(str(raw))
    if cost is not None and (not cost.is_finite() or cost < 0):
        raise ValueError("Invalid legacy cost; resolve it before cutover")
    key = hashlib.sha256(encode((day, str(old["run_id"])))).hexdigest()
    created = old["started_at"]
    if isinstance(created, datetime):
        created = int((created.astimezone(UTC) if created.tzinfo else created.replace(tzinfo=UTC)).timestamp() * 1_000_000)
    return dict(
        run_id="legacy/" + key,
        entry_id="legacy-total",
        day=day,
        mode=str(old["mode"]),
        status=str(old["status"]),
        operation="legacy-unsplit",
        cost_rub=None if cost is None else str(cost),
        created_at=created,
        payload=encode(
            dict(
                legacy_run_id=str(old["run_id"]),
                legacy_day=day,
                note="Historical total; operation split unavailable",
            )
        ),
    )


def totals(rows):
    result = defaultdict(lambda: [0, Decimal(0), 0])
    for row in rows:
        value = result[row["day"]]
        value[0] += 1
        if row["cost_rub"] is None:
            value[2] += 1
        else:
            value[1] += Decimal(row["cost_rub"])
    return dict(result)


def migrate(session, database, suffix):
    """Stage, validate count/day sums/unknowns, then rename in one SDK operation."""
    prefix = database.rstrip("/") + "/"
    descriptions = {name: session.describe_table(prefix + name) for name in schema()}
    if list(descriptions["runs"].primary_key) != ["run_day", "run_id"]:
        raise ValueError("Expected legacy runs PK (run_day,run_id); no automatic schema guessing")
    if list(descriptions["run_objects"].primary_key) != ["run_id", "object_key", "part_no"]:
        raise ValueError("Expected legacy run_objects PK (run_id,object_key,part_no)")
    staged = {name: prefix + name + "_t15_" + suffix for name in schema()}
    for name, description in schema().items():
        session.create_table(staged[name], description)
    rows = []
    for part in session.read_table(prefix + "runs", ordered=True):
        rows.extend(financial_row(dict(row)) for row in part.rows)
    # Prepared types match final schema. Stable PK means no double count on SDK replay.
    columns = tuple(rows[0]) if rows else ()
    if rows:
        declarations = " ".join(
            f"DECLARE ${name} AS "
            + ("Timestamp" if name == "created_at" else "String" if name == "payload" else "Utf8?")
            + ";"
            for name in columns
        )
        query = session.prepare(
            declarations
            + f" UPSERT INTO `{staged['runs']}` ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("$" + n for n in columns)
            + ");"
        )
        for row in rows:
            session.transaction(ydb.SerializableReadWrite()).execute(
                query, {"$" + k: v for k, v in row.items()}, commit_tx=True
            )
    loaded = []
    for part in session.read_table(staged["runs"], ordered=True):
        loaded.extend(dict(row) for row in part.rows)
    if totals(loaded) != totals(rows):
        raise RuntimeError("Staged financial count/day sums/unknown counts differ; cutover refused")
    print(json.dumps(totals(rows), default=str, indent=2))
    moves = []
    for name in schema():
        moves.append(
            SimpleNamespace(
                source_path=prefix + name,
                destination_path=prefix + name + "_preT15_" + suffix,
                replace_destination=False,
            )
        )
        moves.append(
            SimpleNamespace(
                source_path=staged[name], destination_path=prefix + name, replace_destination=False
            )
        )
    session.rename_tables(moves)
    print("Cutover completed. Original tables retained with _preT15_" + suffix + " suffix.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--new", action="store_true", help="Create absent final tables only")
    choice.add_argument("--migrate-legacy", metavar="ARCHIVE_SUFFIX")
    args = parser.parse_args()
    if args.migrate_legacy and not re.fullmatch("[A-Za-z0-9_]+", args.migrate_legacy):
        parser.error("Archive suffix must contain only letters, digits, underscores")
    driver = make_ydb_driver()
    pool = ydb.SessionPool(driver)
    try:
        database = os.environ.get(
            "YDBDOC_YDB_DATABASE", "/ru-central1/b1g7gqj2vnq67gjseuva/etns0641qf73btm7j21k"
        )
        if args.new:
            for name, description in schema().items():
                pool.retry_operation_sync(
                    lambda session, n=name, d=description: session.create_table(
                        database.rstrip("/") + "/" + n, d
                    )
                )
        else:
            # Migration intentionally does not retry a multi-step DDL operation blindly.
            with pool.checkout() as session:
                migrate(session, database, args.migrate_legacy)
    finally:
        pool.stop()
        driver.stop()


if __name__ == "__main__":
    main()
