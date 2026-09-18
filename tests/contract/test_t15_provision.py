"""Offline financial migration conservation; no driver or database is created."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scripts.provision_ydb import financial_row, totals


def test_historical_totals_unknowns_days_and_unique_ids_survive():
    old = dict(
        run_id="same",
        run_day="2026-09-18",
        mode="doc_verify",
        status="RED",
        cost_rub=Decimal("7.250001"),
        started_at=datetime(2026, 9, 18, tzinfo=UTC),
    )
    a = financial_row(old)
    b = financial_row(old | dict(run_day="2026-09-17", cost_rub=None))
    assert a["run_id"] != b["run_id"]
    assert a["entry_id"] == "legacy-total" and a["operation"] == "legacy-unsplit"
    assert a["cost_rub"] == "7.250001" and b["cost_rub"] is None
    assert financial_row(old) == a
    assert totals([a, b]) == {
        "2026-09-18": [1, Decimal("7.250001"), 0],
        "2026-09-17": [1, Decimal(0), 1],
    }


@pytest.mark.parametrize("cost", ["NaN", "Infinity", "-1"])
def test_invalid_history_is_not_silently_replaced(cost):
    with pytest.raises(ValueError):
        financial_row(
            dict(
                run_id="x",
                run_day="2026-09-18",
                mode="doc_translate",
                status="RED",
                started_at=0,
                cost_rub=cost,
            )
        )


def test_stages_validates_and_preserves_both_original_tables():
    from types import SimpleNamespace

    from ydb import _session_impl

    from scripts.provision_ydb import migrate

    old = dict(
        run_id="old",
        run_day="2026-09-18",
        mode="doc_translate",
        status="RED",
        started_at=datetime(2026, 9, 18, tzinfo=UTC),
        cost_rub=7.25,
    )

    class Session:
        def __init__(self):
            self.tables = {"/db/runs": [old], "/db/run_objects": []}
            self.moves = []
            self.ddl = {}

        def describe_table(self, path):
            return SimpleNamespace(
                primary_key=["run_day", "run_id"]
                if path.endswith("/runs")
                else ["run_id", "object_key", "part_no"]
            )

        def create_table(self, path, description):
            assert path not in self.tables
            self.tables[path] = []
            self.ddl[path] = _session_impl.create_table_request_factory(
                SimpleNamespace(attach_request=lambda request: request), path, description
            )

        def read_table(self, path, **kwargs):
            return [SimpleNamespace(rows=self.tables[path])]

        def prepare(self, query):
            return query

        def transaction(self, mode):
            return self

        def execute(self, query, params, commit_tx):
            assert commit_tx
            self.tables["/db/runs_t15_check"].append(
                {key[1:]: value for key, value in params.items()}
            )

        def rename_tables(self, moves):
            pb = _session_impl.rename_tables_request_factory(
                SimpleNamespace(attach_request=lambda request: request), moves
            )
            self.moves = list(pb.tables)

    session = Session()
    migrate(session, "/db", "check")
    assert session.tables["/db/runs"] == [old]
    assert [(m.source_path, m.destination_path, m.replace_destination) for m in session.moves] == [
        ("/db/runs", "/db/runs_preT15_check", False),
        ("/db/runs_t15_check", "/db/runs", False),
        ("/db/run_objects", "/db/run_objects_preT15_check", False),
        ("/db/run_objects_t15_check", "/db/run_objects", False),
    ]
    assert session.ddl["/db/runs_t15_check"].primary_key == ["run_id", "entry_id"]
    assert (
        session.ddl["/db/run_objects_t15_check"].ttl_settings.date_type_column.expire_after_seconds
        == 14 * 86400
    )


def test_service_account_content_and_failed_driver_cleanup(monkeypatch):
    import ydb

    from ydbdoc_review.ops.ydb_driver import make_ydb_driver

    events = []
    monkeypatch.setattr(
        ydb.iam.ServiceAccountCredentials,
        "from_content",
        lambda raw: events.append(("auth", raw)) or "credentials",
    )

    class Driver:
        def __init__(self, **kw):
            events.append(("driver", kw))

        def wait(self, **kw):
            raise RuntimeError("connection unavailable")

        def stop(self):
            events.append(("stop",))

    monkeypatch.setattr(ydb, "Driver", Driver)
    with pytest.raises(RuntimeError, match="connection unavailable"):
        make_ydb_driver(env={"YDB_SA_KEY": '{"id":"dummy"}'})
    assert events[0] == ("auth", '{"id":"dummy"}')
    assert events[-1] == ("stop",)
    events.clear()
    with pytest.raises(RuntimeError, match="YDB_SA_KEY"):
        make_ydb_driver(env={})
    assert events == []
