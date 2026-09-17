"""T12 request replay regressions; no network, Git mutations, or live YDB.

The ledger SQL is executed by SQLite after removing YQL declarations and mapping
UPSERT to INSERT OR REPLACE. This checks the actual relational predicate instead
of making a fake preserve rows merely because a query contains a guard keyword.
SDK transaction mode/commit are still checked by the independent boundary.
SQLite is an offline predicate oracle, not a claim of server-side YQL validation.
"""
import re
import sqlite3
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
import ydb

from tests.contract.test_t12_independent import SDKBoundary, attempt, chat, run_store, transport
from ydbdoc_review.runner import RunResult
from ydbdoc_review.store import StorageError, YDBStore


class SQLBoundary(SDKBoundary):
    def __init__(self):
        super().__init__()
        self.sql_db = sqlite3.connect(':memory:')
        self.sql_db.row_factory = sqlite3.Row
        self.sql_db.execute('''CREATE TABLE runs (
            run_id TEXT, entry_id TEXT, day TEXT, mode TEXT, status TEXT,
            operation TEXT, cost_rub TEXT, created_at INTEGER, payload BLOB,
            PRIMARY KEY (run_id, entry_id))''')
        self.before_pending = None

    def execute(self, sql, params, commit_tx):
        if 'UPSERT INTO runs' not in sql:
            return super().execute(sql, params, commit_tx)
        assert commit_tx
        p = {key.lstrip('$'): value for key, value in params.items()}
        self.calls.append((sql, deepcopy(p)))
        if self.fail(sql, p):
            raise OSError('injected SDK failure')
        if p['status'] == 'pending' and self.before_pending:
            callback, self.before_pending = self.before_pending, None
            callback()
        statement = re.sub(r'DECLARE\s+\$\w+\s+AS\s+\w+\??;', '', sql)
        statement = statement.replace('UPSERT INTO runs', 'INSERT OR REPLACE INTO runs')
        self.sql_db.execute(statement, p)
        self.sql_db.commit()
        self.ledger = {(row['run_id'], row['entry_id']): dict(row)
                       for row in self.sql_db.execute('SELECT * FROM runs')}
        return []


@pytest.fixture
def db(monkeypatch):
    from datetime import UTC, datetime

    sdk = SQLBoundary()
    monkeypatch.setattr(ydb, 'SessionPool', lambda driver: sdk)
    now = [datetime(2026, 9, 18, 12, tzinfo=UTC)]
    store = YDBStore(object(), clock=lambda: now[0])
    yield store, sdk, now
    sdk.sql_db.close()


@pytest.mark.parametrize('mode', ['doc_translate', 'doc_verify', 'doc_continue'])
@pytest.mark.parametrize('failed', [False, True])
@pytest.mark.parametrize('cost', ['7.25', '0', None])
@pytest.mark.parametrize('restart', [False, True])
def test_terminal_row_and_transcript_survive_request_replay(db, mode, failed, cost, restart):
    store, sdk, now = db
    run = run_store(store, mode)
    a = attempt(now[0], cost)
    if not failed:
        a = replace(a, response_text='full successful response', status_code=200, error=None)
    run.record_request(a.request)
    run.record_attempt(a)
    expected = deepcopy(sdk.ledger)
    transcript = store.get(run.run_id, 'attempt/logical/0')
    if restart:
        # Both wrappers are new; the only surviving information is in storage.
        store = YDBStore(object(), clock=lambda: now[0])
        run = run_store(store, mode, run_id=run.run_id)
    for _ in range(3):
        run.record_request(a.request)
    assert sdk.ledger == expected  # includes status, usage, day, operation, exact cost
    assert store.get(run.run_id, 'attempt/logical/0') == transcript
    assert store.daily_cost() == (None if cost is None else Decimal(cost))


def test_pending_insert_is_idempotent_and_scoped_to_both_key_columns(db):
    store, sdk, now = db
    first, second = run_store(store), run_store(store)
    a = attempt(now[0], '7.25')
    first.record_attempt(a)
    second.record_request(a.request)  # same request id, different run
    fallback = replace(a.request, attempt=1)
    first.record_request(fallback)  # same run, different attempt
    expected = deepcopy(sdk.ledger)
    for run, request in ((first, a.request), (second, a.request), (first, fallback)):
        run.record_request(request)
    assert sdk.ledger == expected
    assert len(sdk.ledger) == 3
    assert sdk.ledger[first.run_id, 'logical/0']['cost_rub'] == '7.25'
    assert sdk.ledger[first.run_id, 'logical/1']['status'] == 'pending'
    assert sdk.ledger[second.run_id, 'logical/0']['status'] == 'pending'
    assert store.daily_cost() is None  # real pending requests are not invented free calls


def test_late_pending_transaction_observes_other_adapter_terminal_write(db):
    store, sdk, now = db
    first = run_store(store)
    second = run_store(YDBStore(object(), clock=lambda: now[0]), run_id=first.run_id)
    a = attempt(now[0], '7.25')
    # Other adapter commits after request-object writes but before pending SQL.
    sdk.before_pending = lambda: second.record_attempt(a)
    first.record_request(a.request)
    assert store.daily_cost() == Decimal('7.25')
    assert sdk.ledger[first.run_id, 'logical/0']['status'] == 'failed'


def test_ledger_only_outcome_survives_restart_replay_and_context_expiry(db):
    store, sdk, now = db
    run = run_store(store)
    a = attempt(now[0], '7.25')
    run.record_request(a.request)
    sdk.fail = lambda sql, p: ('UPSERT INTO run_objects' in sql
                              and p['object_key'].startswith('attempt/'))
    with pytest.raises(StorageError, match='YDB: injected SDK failure'):
        run.record_attempt(a)
    expected = deepcopy(sdk.ledger)
    sdk.fail = lambda sql, p: False
    assert store.get(run.run_id, 'attempt/logical/0') is None
    now[0] += timedelta(days=15)
    restarted = run_store(YDBStore(object(), clock=lambda: now[0]), run_id=run.run_id)
    restarted.record_request(a.request)
    assert sdk.ledger == expected  # no dependence on transcript existence or TTL
    assert store.daily_cost() == 0  # original charge never moves into the replay day


def test_paid_primary_and_fallback_replays_keep_both_attempts_without_double_cost(db, monkeypatch):
    store, sdk, now = db
    calls = transport(monkeypatch, (500, 200))
    run = run_store(store)
    client = run.model_factory(cost_resolver=lambda endpoint, response: Decimal('7.25'))
    assert chat(client).content == 'final'
    records = tuple(client.attempts)
    assert len(calls) == len(records) == 2
    original = deepcopy(sdk.ledger)
    restarted = run_store(YDBStore(object(), clock=lambda: now[0]), run_id=run.run_id)
    for a in reversed(records):
        restarted.record_request(a.request)
        restarted.record_request(a.request)
    assert sdk.ledger == original
    for _ in range(2):
        for a in records:
            restarted.record_attempt(a)
            restarted.record_request(a.request)
        restarted.save(RunResult(attempts=records))
    data = store.context(run.run_id)
    assert data['cost_breakdown']['total'] == store.daily_cost() == Decimal('14.50')
    assert len(data['requests']) == len(data['attempts']) == 2
    assert [a['status_code'] for a in data['attempts']] == [500, 200]
    assert [a['response_text'] for a in data['attempts']] == [a.response_text for a in records]
    assert len(sdk.ledger) == 3  # two attempts plus summary
    client.close()


def test_failed_pending_callback_stops_http_and_does_not_invent_paid_outcome(db, monkeypatch):
    store, sdk, _ = db
    calls = transport(monkeypatch)
    sdk.fail = lambda sql, p: 'UPSERT INTO runs' in sql and p['status'] == 'pending'
    run = run_store(store)
    client = run.model_factory(cost_resolver=lambda endpoint, response: Decimal('7.25'))
    with pytest.raises(StorageError, match='YDB: injected SDK failure') as caught:
        chat(client)
    assert calls == [] and client.attempts == [] and sdk.ledger == {}
    sdk.fail = lambda sql, p: False
    run.save(RunResult(errors=(str(caught.value),)))
    data = store.context(run.run_id)
    assert len(data['requests']) == 1 and data['attempts'] == []
    assert data['result']['errors'] == ['YDB: injected SDK failure']
    assert data['cost_breakdown']['total'] == store.daily_cost() == 0
    client.close()
