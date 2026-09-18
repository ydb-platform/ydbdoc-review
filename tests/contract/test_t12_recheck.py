"""Independent T12 reacceptance: SDK boundary, real SQL predicate, no commits.

SQLite runs the actual T12 statements after DECLARE removal/UPSERT spelling
translation. YDB's real converter additionally checks declared parameter types.
This is deliberately not a YQL compiler or a live-server concurrency test.
"""
import json
import re
import sqlite3
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import ydb
from ydb import convert

from tests.context_records import context_with_records
from tests.contract.test_t12_independent import SDKBoundary, attempt, chat, run_store, transport
from ydbdoc_review.runner import RunResult
from ydbdoc_review.store import BudgetExceeded, StorageError, YDBStore


class PersistentSQL(SDKBoundary):
    """No store-method replacement; all T12 reads/writes execute relational SQL."""
    def __init__(self, path):
        super().__init__()
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT, entry_id TEXT, day TEXT, mode TEXT, status TEXT,
                operation TEXT, cost_rub TEXT, created_at INTEGER, payload BLOB,
                PRIMARY KEY (run_id, entry_id));
            CREATE TABLE IF NOT EXISTS run_objects (
                run_id TEXT, object_key TEXT, generation TEXT, part_no INTEGER,
                created_at INTEGER, payload BLOB,
                PRIMARY KEY (run_id, object_key, generation, part_no));
        ''')
        self.replay_next = False
        self.replayed = 0
        self.typed = []

    def retry_operation_sync(self, callback):
        result = callback(self)
        if self.replay_next and 'UPSERT INTO runs' in self.calls[-1][0]:
            self.replay_next = False
            self.replayed += 1
            # Simulates committed transaction whose ACK was lost, then SDK retry.
            result = callback(self)
        return result

    def execute(self, sql, params, commit_tx):
        assert commit_tx is True
        declarations = re.findall(r'DECLARE\s+(\$\w+)\s+AS\s+(\w+)(\?)?;', sql)
        types = {}
        for name, primitive, optional in declarations:
            value_type = getattr(ydb.PrimitiveType, primitive)
            types[name] = ydb.OptionalType(value_type) if optional else value_type
        assert types.keys() == params.keys()
        wire = convert.parameters_to_pb(types, params)
        assert wire.keys() == params.keys()
        if '$created_at' in wire:
            assert wire['$created_at'].value.uint64_value == params['$created_at']
        if '$payload' in wire:
            assert wire['$payload'].value.bytes_value == params['$payload']
        self.typed.append(wire)
        self.calls.append((sql, dict(params)))
        p = {k.lstrip('$'): v for k, v in params.items()}
        if self.fail(sql, p):
            raise OSError('recheck pending storage unavailable')
        statement = re.sub(r'DECLARE\s+\$\w+\s+AS\s+\w+\??;', '', sql)
        statement = statement.replace('UPSERT INTO', 'INSERT OR REPLACE INTO')
        cursor = self.connection.execute(statement, p)
        rows = [SimpleNamespace(**dict(row)) for row in cursor] if cursor.description else []
        self.connection.commit()
        return [SimpleNamespace(rows=rows, truncated=False)]

    def rows(self):
        return [tuple(row) for row in self.connection.execute(
            'SELECT * FROM runs ORDER BY run_id, entry_id')]


@pytest.fixture
def persistent(tmp_path, monkeypatch):
    path = tmp_path / 'durable.sqlite'
    instances = []
    now = [datetime(2024, 2, 29, 20, 59, 59, 999999, tzinfo=UTC)]

    def reopen():
        sdk = PersistentSQL(path)
        instances.append(sdk)
        monkeypatch.setattr(ydb, 'SessionPool', lambda driver: sdk)
        return YDBStore(object(), clock=lambda: now[0]), sdk

    yield reopen, now
    for sdk in instances:
        sdk.connection.close()


@pytest.mark.parametrize('mode', ['doc_translate', 'doc_verify', 'doc_continue'])
@pytest.mark.parametrize('failed', [False, True])
def test_disk_restart_and_sdk_retry_never_downgrade_terminal(persistent, mode, failed):
    reopen, now = persistent
    store, sdk = reopen()
    run = run_store(store, mode)
    record = attempt(now[0], '7.25')
    if not failed:
        record = replace(record, error=None, status_code=200, response_text='done')
    run.record_request(record.request)
    sdk.replay_next = True
    run.record_attempt(record)
    assert sdk.replayed == 1
    expected = sdk.rows()
    transcript = store.get(run.run_id, 'attempt/logical/0')
    sdk.connection.close()  # no Python table state survives reopening the database
    store, sdk = reopen()
    restarted = run_store(store, mode, run_id=run.run_id)
    for _ in range(3):
        sdk.replay_next = True
        restarted.record_request(record.request)
    assert sdk.rows() == expected
    assert store.daily_cost() == Decimal('7.25')
    assert store.get(run.run_id, 'attempt/logical/0') == transcript
    for _ in range(2):
        restarted.record_attempt(record)
        restarted.save(RunResult(attempts=(record,)))
    assert store.daily_cost() == Decimal('7.25')
    ledger_calls = [(sql, p) for sql, p in sdk.calls if 'UPSERT INTO runs' in sql]
    pending = [sql for sql, p in ledger_calls if p['$status'] == 'pending']
    assert len(pending) == 6  # each pending transaction is replayed after lost ACK
    assert all(sql.count('UPSERT INTO') == 1 for sql in pending)
    assert len(sdk.rows()) == 2  # outcome + excluded summary


def test_real_sql_paid_fallback_failed_repair_restart_and_calendar(persistent, monkeypatch):
    reopen, now = persistent
    store, _sdk = reopen()
    run = run_store(store)
    now[0] = datetime.now(UTC)
    calls = transport(monkeypatch, (500, 200, 400))
    client = run.model_factory(cost_resolver=lambda e, r: Decimal('7.25'))
    run.admit(Decimal('1'))
    assert chat(client).content == 'final'
    from ydbdoc_review.model import ModelError
    with pytest.raises(ModelError):
        chat(client, 'repair')
    records = tuple(client.attempts)
    assert len(calls) == len(records) == 3
    run.admit(Decimal('1'))  # no second budget gate after overrun
    run.save(RunResult(attempts=records, errors=('paid repair failed',)))
    client.close()
    store, _sdk = reopen()
    restarted = run_store(store, run_id=run.run_id)
    for record in reversed(records):
        restarted.record_request(record.request)
    assert store.daily_cost() == Decimal('21.75')
    data = context_with_records(store, run.run_id)
    assert data['cost_breakdown'] == dict(translation=Decimal('14.50'), critic=0,
                                          repair=Decimal('7.25'), total=Decimal('21.75'))
    assert [r['status_code'] for r in data['attempts']] == [500, 200, 400]
    with pytest.raises(BudgetExceeded):
        run_store(store).admit(Decimal('21.75'))
    # Transport timestamps use the real clock; advance to the following MSK day.
    latest = max(r.request.created_at for r in records)
    now[0] = latest + timedelta(days=1)
    assert store.daily_cost() == 0


def test_actual_sql_leap_day_boundary_and_terminal_ttl_independence(persistent):
    reopen, now = persistent
    store, sdk = reopen()
    run = run_store(store)
    record = attempt(now[0], '7.25')
    run.record_attempt(record)
    assert store.daily_cost() == Decimal('7.25')
    now[0] += timedelta(microseconds=1)
    assert now[0].astimezone(UTC).date().isoformat() == '2024-02-29'
    assert store.daily_cost() == 0  # March 1 in Moscow
    expected = sdk.rows()
    sdk.connection.execute('DELETE FROM run_objects')
    sdk.connection.commit()  # physical TTL deletion, not merely a clock check
    store, sdk = reopen()
    run_store(store, run_id=run.run_id).record_request(record.request)
    assert sdk.rows() == expected
    assert store.daily_cost() == 0


def test_sdk_parameter_types_and_pending_failure_before_http(persistent, monkeypatch):
    reopen, _ = persistent
    store, sdk = reopen()
    calls = transport(monkeypatch)
    sdk.fail = lambda sql, p: 'UPSERT INTO runs' in sql and p['status'] == 'pending'
    run = run_store(store)
    client = run.model_factory()
    with pytest.raises(StorageError, match='YDB: recheck pending storage unavailable') as exc:
        chat(client)
    assert calls == [] and client.attempts == []
    pending_wire = sdk.typed[-1]
    assert pending_wire['$cost_rub'].type.HasField('optional_type')
    assert pending_wire['$cost_rub'].value.HasField('null_flag_value')
    sdk.fail = lambda sql, p: False
    run.save(RunResult(errors=(str(exc.value),)))
    context = context_with_records(store, run.run_id)
    assert context['result']['errors'] == [str(exc.value)]
    assert len(context['requests']) == 1 and context['attempts'] == []
    assert context['cost_breakdown']['total'] == store.daily_cost() == 0
    client.close()


@pytest.mark.parametrize('mechanical', [False, True])
def test_actual_runner_without_commits_or_budget_dependency(persistent, monkeypatch, mechanical):
    from ydbdoc_review.build import build_candidate
    from ydbdoc_review.config.loader import Settings
    from ydbdoc_review.document import RequestBudget
    from ydbdoc_review.github.client import GitHubClient
    from ydbdoc_review.model import Endpoint, ModelChoice
    from ydbdoc_review.publication import Publisher
    from ydbdoc_review.runner import run_translate

    reopen, _ = persistent
    store, sdk = reopen()
    sdk.fail = lambda q, p: 'SELECT run_id, entry_id, cost_rub' in q
    repo = Path(__file__).resolve().parents[2]
    sha = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip()
    path = 'ydb/docs/ru/__t12_recheck_absent__.md'
    changes = [dict(filename=path, status='removed')] if mechanical else []
    calls = []

    def send(session, request, **kwargs):
        calls.append(request.url)
        assert request.method == 'GET'  # no publication or paid HTTP
        response = requests.Response()
        response.status_code = 200
        if '/git/ref/' in request.url or '/branches/' in request.url:
            response.status_code, body = 404, {'message': 'Not Found'}
        elif request.url.split('?')[0].endswith('/files'):
            body = changes
        else:
            body = dict(state='open', merged=False, head=dict(sha=sha, ref='topic',
                        repo=dict(full_name='o/r')), base=dict(ref='main'))
        response._content = json.dumps(body).encode()
        return response

    monkeypatch.setattr(requests.Session, 'send', send)
    github = GitHubClient('dummy')
    publisher = Publisher(github, 'o/r', 'https://github.com/o/r.git', 'translation', 'dummy')
    run = run_store(store)
    choice = ModelChoice(Endpoint('eliza', 'https://invalid.test', 'main', 'dummy'))
    result = run_translate(
        repo=repo, github=github, owner='o', repository='r', pr_number=1, actor='writer',
        settings=Settings(20, 250000, frozenset({'writer'}), Decimal(0), 'endpoint', 'db', 'dummy'),
        publisher=publisher, model_factory=lambda: pytest.fail('unpaid runner created model'),
        admit=lambda: run.admit(Decimal(0)), translation_choice=choice, critic_choice=choice,
        repair_choice=choice, budget=RequestBudget(10000, 1000, lambda m: len(str(m))),
        hooks=run.hooks(), build=partial(build_candidate, executable='/nonexistent/t12-yfm'))
    context = context_with_records(store, run.run_id)
    assert context['cost_breakdown']['total'] == 0
    assert not any('SELECT run_id, entry_id, cost_rub' in q for q, _ in sdk.calls)
    if mechanical:
        assert result.plan.operations[0].kind == 'deleted'
        assert not result.plan.needs_model
        assert result.candidate.sha == result.checked_sha == sha
        assert result.status == 'RED'  # actual missing-build error, not fake GREEN
        assert any('YFM executable is missing' in issue.problem for issue in result.issues)
        assert context['final_files'][path.replace('/ru/', '/en/')] is None
    else:
        assert result.status == 'NO_WORK'
    assert result.publication is None
