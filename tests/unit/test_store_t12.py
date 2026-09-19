"""Offline real SDK schema + stateful Session/transaction boundary, never live YDB."""
# ruff: noqa: RUF001 -- Unicode roundtrip fixture.
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import requests
import ydb

from tests.context_records import context_with_records
from ydbdoc_review import store as module
from ydbdoc_review.model import AttemptRecord, Endpoint, ModelChoice, RequestRecord, Usage
from ydbdoc_review.runner import RunResult, finalize
from ydbdoc_review.store import (
    BudgetExceeded,
    BudgetUnknown,
    ContextExpired,
    RunStore,
    StorageError,
    YDBStore,
    cost_breakdown,
    encode,
    rub_resolver,
    schema,
)


class Boundary:
    """Emulates the four SQL operations, retaining table state across new adapters."""
    def __init__(self):
        self.objects = {}
        self.runs = {}
        self.calls = []
        self.schemas = {}
        self.fail = lambda q, p: False
        self.stopped = False

    def retry_operation_sync(self, callback):
        return callback(self)

    def prepare(self, query):
        return query

    def transaction(self, mode):
        assert isinstance(mode, ydb.SerializableReadWrite)
        return self

    def execute(self, query, params, commit_tx):
        assert commit_tx
        p = {k.removeprefix('$'): v for k, v in params.items()}
        self.calls.append((query, deepcopy(p)))
        if self.fail(query, p):
            raise OSError('YDB offline injected')
        if 'UPSERT INTO run_objects' in query:
            self.objects[tuple(p[n] for n in ('run_id', 'object_key', 'generation', 'part_no'))] = p
            return []
        if 'UPSERT INTO runs' in query:
            if ('LEFT JOIN runs AS previous' in query
                    and (p['run_id'], p['entry_id']) in self.runs):
                return []
            self.runs[p['run_id'], p['entry_id']] = p
            return []
        if 'FROM run_objects' in query:
            rows = [SimpleNamespace(**v) for v in self.objects.values()
                    if all(v[n] == p[n] for n in ('run_id', 'object_key', 'generation'))]
            rows = [r for r in sorted(rows, key=lambda r: r.part_no)
                    if r.part_no >= p['start']][:p['limit']]
            return [SimpleNamespace(rows=rows)]
        if 'FROM runs' in query:
            rows = [SimpleNamespace(**v) for v in self.runs.values()
                    if v['day'] == p['day'] and v['entry_id'] != 'summary'
                    and (v['run_id'], v['entry_id']) > (p['after_run'], p['after_entry'])]
            rows = sorted(rows, key=lambda r: (r.run_id, r.entry_id))[:1000]
            return [SimpleNamespace(rows=rows)]
        raise AssertionError(query)

    def create_table(self, path, description):
        self.schemas[path] = description

    def stop(self):
        self.stopped = True


@pytest.fixture
def db(monkeypatch):
    boundary = Boundary()
    monkeypatch.setattr(ydb, 'SessionPool', lambda driver: boundary)
    now = [datetime(2026, 9, 18, 20, 59, 59, tzinfo=UTC)]
    store = YDBStore(boundary, clock=lambda: now[0])
    return store, boundary, now


def request(*, id='req', attempt=0, operation='translation', created=None):
    return RequestRecord(id, operation, 'eliza', 'model', 'https://example.invalid',
                         {'messages': [{'role': 'user', 'content': 'Русский\r\n𐍈'}]},
                         created or datetime(2026, 9, 18, 20, 59, 59, tzinfo=UTC), attempt)


def paid(cost='1.1234567890123456789', **kwargs):
    return AttemptRecord(request(**kwargs), '{"full":"response"}', 500, 'HTTP 500',
                         Usage(100, 200, None if cost is None else Decimal(cost),
                               {'prompt_tokens': 100, 'completion_tokens': 200}))


def adapter(store, **kwargs):
    return RunStore(store, source_pr='o/r/1', mode=kwargs.pop('mode', 'doc_translate'), **kwargs)


def test_real_sdk_schema_ttl_and_provisioning(db):
    store, boundary, _ = db
    store.create_schema('/database')
    tables = boundary.schemas
    objects = tables['/database/run_objects']
    from ydb import _session_impl
    state = SimpleNamespace(attach_request=lambda request: request)
    request_pb = _session_impl.create_table_request_factory(state, '/database/run_objects', objects)
    request_pb = type(request_pb).FromString(request_pb.SerializeToString())
    pb = request_pb.ttl_settings
    assert pb.WhichOneof('mode') == 'date_type_column'
    assert pb.date_type_column.column_name == 'created_at'
    assert pb.date_type_column.expire_after_seconds == 14 * 24 * 3600
    assert objects.primary_key == ['run_id', 'object_key', 'generation', 'part_no']
    assert tables['/database/runs'].ttl_settings is None
    columns = {c.name: c.type for c in objects.columns}
    assert columns['created_at'].proto.optional_type.item.type_id == ydb.PrimitiveType.Timestamp.proto.type_id
    assert set(schema()) == {'runs', 'run_objects'}


@pytest.mark.parametrize('payload', [b'', b'\0\xff\r\n', ('𐍈\r\nПривет' * 80000).encode()], ids=['empty', 'binary', 'large-unicode'])
def test_chunk_roundtrip_and_shorter_overwrite(db, payload):
    store, _, _ = db
    store.put('run', 'context', payload)
    assert store.get('run', 'context') == payload
    store.put('run', 'context', b'x')
    assert store.get('run', 'context') == b'x'
    assert store.get('run', 'missing') is None


def test_atomic_manifest_failure_keeps_old_context(db):
    store, boundary, _ = db
    store.put('r', 'context', b'old')
    boundary.fail = lambda q, p: 'UPSERT INTO run_objects' in q and p['generation'] == ''
    with pytest.raises(StorageError):
        store.put('r', 'context', b'new')
    boundary.fail = lambda q, p: False
    assert store.get('r', 'context') == b'old'


@pytest.mark.parametrize('corruption', ['missing', 'bytes'])
def test_partial_or_corrupt_chunks_are_storage_errors(db, corruption):
    store, boundary, _ = db
    store.put('r', 'context', encode({'value': 'full'}))
    key = next(k for k in boundary.objects if k[2])
    if corruption == 'missing':
        del boundary.objects[key]
    else:
        boundary.objects[key]['payload'] = b'wrong'
    with pytest.raises(StorageError):
        store.context('r')


def test_ttl_exact_boundary_and_error_not_expired(db):
    store, boundary, now = db
    store.put('r', 'context', encode({'schema_version': 1, 'empty': b''}))
    now[0] += timedelta(days=14, microseconds=-1)
    assert store.context('r') == {'schema_version': 1, 'empty': b''}
    now[0] += timedelta(microseconds=1)
    with pytest.raises(ContextExpired, match='14'):
        store.context('r')
    boundary.fail = lambda q, p: True
    with pytest.raises(StorageError, match='YDB'):
        store.context('r')


@pytest.mark.parametrize('mode', ['doc_translate', 'doc_verify', 'doc_continue'])
def test_callbacks_save_failed_cancel_and_exact_cost(db, mode):
    store, boundary, _ = db
    run = adapter(store, mode=mode, continuation_count=2)
    a = paid()
    run.record_request(a.request)
    run.record_attempt(a)
    result = RunResult(mode=mode, status='RED', cancelled=True, errors=('cancel',), attempts=(a,))
    assert finalize(result, run.hooks()).errors == ('cancel',)
    context = context_with_records(store, run.run_id)
    assert context['result']['cancelled']
    assert context['result']['errors'] == ['cancel']
    assert context['continuation_count'] == 2
    assert context['cost_breakdown']['total'] == a.usage.cost_rub
    assert context['requests'][0]['payload'] == a.request.payload
    assert context['attempts'][0]['response_text'] == a.response_text
    assert context['attempts'][0]['usage']['raw'] == a.usage.raw
    assert store.daily_cost() == a.usage.cost_rub
    assert len(boundary.runs) == 2  # idempotent callback/final-save, summary not billed twice


def test_midnight_all_modes_one_gate_overrun_next_blocked(db):
    store, boundary, now = db
    for mode in ('doc_translate', 'doc_verify', 'doc_continue'):
        adapter(store, mode=mode).record_attempt(paid('1'))
    assert store.daily_cost() == Decimal(3)
    first, parallel = adapter(store), adapter(store)
    first.admit(Decimal(4))
    parallel.admit(Decimal(4))  # no reservations
    first.record_attempt(paid('20', id='big'))
    before = len(boundary.calls)
    first.admit(Decimal(4))  # no repeat, admitted work may overrun
    assert len(boundary.calls) == before
    with pytest.raises(BudgetExceeded, match=r'23.*4.*YDBDOC_DAILY_BUDGET_RUB'):
        adapter(store).admit(Decimal(4))
    now[0] += timedelta(seconds=1)  # UTC 21:00 = Moscow next date
    assert store.daily_cost() == 0
    adapter(store).admit(Decimal(4))
    # An attempt ending on next day belongs to request day; no finish-time rebucketing.
    first.record_attempt(paid('21', id='big'))
    assert store.daily_cost() == 0


@pytest.mark.parametrize('limit', ['0', '3'])
def test_budget_greater_equal(db, limit):
    store, _, _ = db
    if limit == '3':
        adapter(store).record_attempt(paid('3'))
    with pytest.raises(BudgetExceeded):
        adapter(store).admit(Decimal(limit))


def test_unknown_cost_pending_and_failed_keeps_records(db):
    store, _, _ = db
    run = adapter(store)
    a = paid(None)
    run.record_request(a.request)
    assert store.daily_cost() is None
    run.record_attempt(a)
    run.save(RunResult(attempts=(a,)))
    context = context_with_records(store, run.run_id)
    assert context['cost_breakdown'] == {'translation': None, 'critic': Decimal(0),
                                         'repair': Decimal(0), 'total': None}
    with pytest.raises(BudgetUnknown):
        adapter(store).admit(Decimal(100))


def test_write_failure_visible_and_final_reconciles_attempt(db):
    store, boundary, _ = db
    run = adapter(store)
    a = paid('9')
    boundary.fail = lambda q, p: 'UPSERT INTO run_objects' in q
    with pytest.raises(StorageError):
        run.record_attempt(a)
    assert store.daily_cost() == 9  # money survived failed transcript
    result = finalize(RunResult(attempts=(a,)), run.hooks())
    assert any('storage' in error for error in result.errors)
    boundary.fail = lambda q, p: False
    run.save(result)
    context = context_with_records(store, run.run_id)
    assert context['attempts'][0]['usage']['cost_rub'] == 9
    assert context['requests'][0]['id'] == a.request.id


def test_factory_request_attempt_real_transport_fallback(db, monkeypatch):
    store, _, _ = db
    run = adapter(store)
    calls = []
    def send(session, prepared, **kwargs):
        calls.append(prepared)
        response = requests.Response()
        response.status_code = 500 if len(calls) == 1 else 200
        response._content = (b'{"error":"failed","usage":{"prompt_tokens":2,"completion_tokens":1}}'
                             if len(calls) == 1 else
                             b'{"choices":[{"message":{"content":"translated"}}],"usage":{"prompt_tokens":2,"completion_tokens":1}}')
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    main = Endpoint('eliza', 'https://example.invalid', 'one', 'secret')
    alt = replace(main, model='two')
    client = run.model_factory(cost_resolver=rub_resolver(extract=lambda e, r: Decimal('2.5')))
    response = client.chat([{'role': 'user', 'content': 'text'}], operation='translation',
                           choice=ModelChoice(main, alt), max_tokens=100)
    assert response.content == 'translated'
    run.save(RunResult(attempts=tuple(client.attempts)))
    client.close()
    context = context_with_records(store, run.run_id)
    assert len(context['requests']) == len(context['attempts']) == 2
    assert context['cost_breakdown']['total'] == 5
    assert context['attempts'][0]['error']
    assert b'secret' not in encode(context)


def test_resolver_explicit_tariffs_unknown_and_precise():
    endpoint = Endpoint('eliza', 'https://example.invalid', 'one', 'secret')
    data = {'usage': {'prompt_tokens': 2, 'completion_tokens': 3}}
    assert rub_resolver()(endpoint, data) is None
    resolver = rub_resolver(tariffs={('eliza', 'one'): (Decimal('1.2'), Decimal('2.3'))})
    assert resolver(endpoint, data) == Decimal('0.0000093')
    assert resolver(endpoint, {'usage': {'prompt_tokens': 2}}) is None
    assert resolver(endpoint, {'usage': {'prompt_tokens': 0, 'completion_tokens': 0}}) == 0
    with pytest.raises(ValueError):
        rub_resolver(extract=lambda e, r: Decimal('NaN'))(endpoint, data)
    assert cost_breakdown([paid('1'), paid(None, operation='repair')])['total'] is None


def test_no_work_save_does_not_query_budget(db):
    store, boundary, _ = db
    run = adapter(store)
    boundary.fail = lambda q, p: 'FROM runs' in q
    result = finalize(RunResult(status='NO_WORK'), run.hooks())
    assert result.status == 'NO_WORK'
    assert context_with_records(store, run.run_id)['cost_breakdown']['total'] == 0


def test_production_factory_never_null(monkeypatch):
    def failed(**kwargs):
        raise RuntimeError('missing key')
    monkeypatch.setattr(module, 'make_ydb_driver', failed)
    with pytest.raises(StorageError, match='missing key'):
        module.create_store()


def test_budget_pagination_no_silent_1000_row_cutoff(db):
    store, boundary, now = db
    for i in range(1002):
        store.ledger(f'{i:05}', 'attempt', created=now[0], mode='doc_verify',
                     status='failed', cost=Decimal('0.1'))
    assert store.daily_cost() == Decimal('100.2')
    assert sum('FROM runs' in q for q, _ in boundary.calls) == 2


def test_pending_outcome_cannot_be_reported_as_zero(db):
    store, _, _ = db
    run = adapter(store)
    run.record_request(request())
    run.save(RunResult(cancelled=True))
    assert context_with_records(store, run.run_id)['cost_breakdown']['total'] is None


def test_budget_error_is_cached_and_not_expiry(db):
    store, boundary, _ = db
    boundary.fail = lambda q, p: 'FROM runs' in q
    run = adapter(store)
    for _ in range(2):
        with pytest.raises(StorageError):
            run.admit(Decimal(10))
    assert len(boundary.calls) == 1


def test_invalid_stored_billing_is_storage_error(db):
    store, boundary, _ = db
    run = adapter(store)
    run.record_attempt(paid())
    next(iter(boundary.runs.values()))['cost_rub'] = 'NaN'
    with pytest.raises(StorageError):
        store.daily_cost()


@pytest.mark.parametrize('error', [KeyboardInterrupt, RuntimeError])
def test_factory_inflight_interrupt_retains_unknown_attempt(db, monkeypatch, error):
    from tests.model_clock import model_clock
    store, _, now = db
    model_clock(monkeypatch, now)
    run = adapter(store)
    def send(*args, **kwargs):
        raise error('interrupted in HTTP')
    monkeypatch.setattr(requests.Session, 'send', send)
    client = run.model_factory()
    choice = ModelChoice(Endpoint('eliza', 'https://example.invalid', 'one', 'secret'))
    with pytest.raises(error):
        client.chat([{'role': 'user', 'content': 'text'}], operation='repair',
                    choice=choice, max_tokens=100)
    assert len(client.attempts) == 1
    assert client.cost_breakdown()['total'] is None
    run.save(RunResult(cancelled=True, attempts=tuple(client.attempts),
                       cost_breakdown=client.cost_breakdown()))
    data = context_with_records(store, run.run_id)
    assert data['attempts'][0]['error'] == f'{error.__name__}: outcome unavailable'
    assert data['attempts'][0]['response_text'] is None
    assert data['result']['cost_breakdown']['total'] is None
    assert store.daily_cost() is None
    client.close()


def test_factory_failed_request_storage_prevents_http_without_fake_paid_attempt(db, monkeypatch):
    store, boundary, _ = db
    run = adapter(store)
    boundary.fail = lambda q, p: 'UPSERT INTO runs' in q
    monkeypatch.setattr(requests.Session, 'send', lambda *a, **kw: pytest.fail('HTTP after storage error'))
    client = run.model_factory()
    choice = ModelChoice(Endpoint('eliza', 'https://example.invalid', 'one', 'secret'))
    with pytest.raises(StorageError):
        client.chat([{'role': 'user', 'content': 'text'}], operation='repair',
                    choice=choice, max_tokens=100)
    assert client.attempts == []
    assert client.cost_breakdown()['total'] == 0
    client.close()


def test_interrupt_and_attempt_storage_failure_retains_cancel_and_unknown(db, monkeypatch):
    store, boundary, _ = db
    run = adapter(store)
    def send(*args, **kwargs):
        boundary.fail = lambda q, p: 'UPSERT INTO runs' in q
        raise KeyboardInterrupt()
    monkeypatch.setattr(requests.Session, 'send', send)
    client = run.model_factory()
    choice = ModelChoice(Endpoint('eliza', 'https://example.invalid', 'one', 'secret'))
    with pytest.raises(KeyboardInterrupt, match='storage'):
        client.chat([{'role': 'user', 'content': 'text'}], operation='repair',
                    choice=choice, max_tokens=100)
    assert client.cost_breakdown()['total'] is None
    boundary.fail = lambda q, p: False
    run.save(RunResult(cancelled=True, attempts=tuple(client.attempts)))
    assert context_with_records(store, run.run_id)['cost_breakdown']['total'] is None
    client.close()
