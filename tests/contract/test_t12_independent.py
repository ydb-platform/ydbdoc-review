"""Independent T12 checks: real store, SDK descriptions, adapters and callbacks.

Only YDB SessionPool and HTTP send are substituted. No store mocks, live services,
Git mutations, default prices, or continuation-count policy.
"""
import json
import subprocess
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import ydb
from ydb import _session_impl

from tests.context_records import context_with_records
from ydbdoc_review.document import FileResult
from ydbdoc_review.links import Candidate
from ydbdoc_review.model import (
    AttemptRecord,
    Endpoint,
    ModelChoice,
    ModelError,
    RequestRecord,
    Usage,
)
from ydbdoc_review.plan import Snapshot
from ydbdoc_review.publication import Publication
from ydbdoc_review.quality import Issue
from ydbdoc_review.quality_loop import SelectedFile
from ydbdoc_review.runner import RunResult, finalize
from ydbdoc_review.store import (
    BudgetExceeded,
    BudgetUnknown,
    ContextExpired,
    RunStore,
    StorageError,
    YDBStore,
    encode,
    rub_resolver,
)


class SDKBoundary:
    """Stateful relational keys at the public SessionPool transaction boundary."""
    def __init__(self):
        self.objects, self.ledger, self.ddl, self.calls = {}, {}, {}, []
        self.fail = lambda query, params: False
        self.truncated = False

    def retry_operation_sync(self, callback):
        return callback(self)

    def prepare(self, sql):
        return sql

    def transaction(self, mode):
        assert isinstance(mode, ydb.SerializableReadWrite)
        return self

    def create_table(self, path, description):
        pb = _session_impl.create_table_request_factory(
            SimpleNamespace(attach_request=lambda value: value), path, description)
        self.ddl[path] = type(pb).FromString(pb.SerializeToString())

    def execute(self, sql, params, commit_tx):
        assert commit_tx
        p = {key.lstrip('$'): value for key, value in params.items()}
        self.calls.append((sql, deepcopy(p)))
        if self.fail(sql, p):
            raise OSError('injected SDK failure')
        if 'UPSERT INTO run_objects' in sql:
            key = tuple(p[k] for k in ('run_id', 'object_key', 'generation', 'part_no'))
            self.objects[key] = deepcopy(p)
            return []
        if 'UPSERT INTO runs' in sql:
            # Emulate the atomic insert-if-absent request write at the SDK boundary.
            if ('LEFT JOIN runs AS previous' in sql
                    and (p['run_id'], p['entry_id']) in self.ledger):
                return []
            self.ledger[p['run_id'], p['entry_id']] = deepcopy(p)
            return []
        if 'FROM run_objects' in sql:
            rows = [v for k, v in sorted(self.objects.items())
                    if k[:3] == (p['run_id'], p['object_key'], p['generation'])
                    and k[3] >= p['start']][:p['limit']]
        elif 'FROM runs' in sql:
            rows = [v for k, v in sorted(self.ledger.items())
                    if v['day'] == p['day'] and k[1] != 'summary'
                    and k > (p['after_run'], p['after_entry'])][:1000]
        else:
            raise AssertionError(sql)
        return [SimpleNamespace(rows=[SimpleNamespace(**deepcopy(r)) for r in rows],
                                truncated=self.truncated)]


@pytest.fixture
def db(monkeypatch):
    sdk = SDKBoundary()
    monkeypatch.setattr(ydb, 'SessionPool', lambda driver: sdk)
    now = [datetime.now(UTC)]
    store = YDBStore(object(), clock=lambda: now[0])
    return store, sdk, now


def run_store(store, mode='doc_translate', **kwargs):
    return RunStore(store, mode=mode, source_pr='owner/repo/123', **kwargs)


def attempt(now, cost='0.125', operation='translation', id='logical', index=0):
    request = RequestRecord(id, operation, 'eliza', 'fixture', 'https://invalid.test',
                            {'messages': [{'role': 'user', 'content': 'source\r\n\u0000'}]}, now, index)
    return AttemptRecord(request, '{"error":"paid failure"}', 500, 'HTTP 500',
                         Usage(2, 3, None if cost is None else Decimal(cost),
                               {'prompt_tokens': 2, 'completion_tokens': 3}))


def chat(client, operation='translation'):
    main = Endpoint('eliza', 'https://invalid.test', 'main', 'dummy')
    return client.chat([{'role': 'user', 'content': 'source'}], operation=operation,
                       choice=ModelChoice(main, replace(main, model='alternative')), max_tokens=20)


def transport(monkeypatch, statuses=(200,), after=None, missing_usage=False):
    calls = []
    def send(session, request, **kwargs):
        calls.append(request)
        response = requests.Response()
        response.status_code = statuses[min(len(calls) - 1, len(statuses) - 1)]
        response._content = b'{"choices":[{"message":{"content":"final"}}],"usage":{"prompt_tokens":2,"completion_tokens":3}}'
        if missing_usage:
            body = json.loads(response._content)
            del body["usage"]
            response._content = json.dumps(body).encode()
        if after:
            after(len(calls))
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    return calls


def test_schema_serialized_through_actual_sdk(db):
    store, sdk, _ = db
    store.create_schema('/fixture/db/')
    assert set(sdk.ddl) == {'/fixture/db/runs', '/fixture/db/run_objects'}
    objects = sdk.ddl['/fixture/db/run_objects']
    assert list(objects.primary_key) == ['run_id', 'object_key', 'generation', 'part_no']
    assert list(sdk.ddl['/fixture/db/runs'].primary_key) == ['run_id', 'entry_id']
    assert objects.ttl_settings.date_type_column.column_name == 'created_at'
    assert objects.ttl_settings.date_type_column.expire_after_seconds == 1209600
    assert not sdk.ddl['/fixture/db/runs'].HasField('ttl_settings')
    columns = {c.name: c.type.optional_type.item.type_id for c in objects.columns}
    assert columns['created_at'] == ydb.PrimitiveType.Timestamp.proto.type_id
    assert columns['payload'] == ydb.PrimitiveType.String.proto.type_id


@pytest.mark.parametrize('size', [0, 1, 524287, 524288, 524289, 1048613])
def test_exact_bytes_and_short_overwrite(db, size):
    store, _, _ = db
    payload = (bytes(range(256)) * (size // 256 + 1))[:size]
    store.put('r', 'context', payload)
    assert store.get('r', 'context') == payload
    store.put('r', 'context', b'\x00')
    assert store.get('r', 'context') == b'\x00'


@pytest.mark.parametrize('phase', ['chunk', 'manifest'])
def test_failed_overwrite_preserves_committed_generation(db, phase):
    store, sdk, _ = db
    store.put('r', 'context', b'previous')
    sdk.fail = lambda q, p: 'UPSERT INTO run_objects' in q and (
        p['part_no'] == 1 if phase == 'chunk' else p['generation'] == '')
    with pytest.raises(StorageError):
        store.put('r', 'context', b'x' * 600000)
    sdk.fail = lambda q, p: False
    assert store.get('r', 'context') == b'previous'


@pytest.mark.parametrize('damage', ['missing', 'bytes', 'number', 'manifest', 'truncated'])
def test_damage_is_storage_failure_not_expiry(db, damage):
    store, sdk, _ = db
    store.put('r', 'context', encode({'source': 'exact'}))
    key = next(k for k in sdk.objects if k[2])
    if damage == 'missing':
        del sdk.objects[key]
    elif damage == 'bytes':
        sdk.objects[key]['payload'] = b'corruption'
    elif damage == 'number':
        sdk.objects[key]['part_no'] = 9
    elif damage == 'manifest':
        sdk.objects['r', 'context', '', 0]['payload'] = b'not json'
    else:
        sdk.truncated = True
    with pytest.raises(StorageError):
        store.context('r')


def test_ttl_missing_outage_distinction(db):
    store, sdk, now = db
    with pytest.raises(ContextExpired, match='14'):
        store.context('missing')
    store.put('r', 'context', encode({'schema_version': 1, 'empty': b'', 'deleted': None}))
    now[0] += timedelta(days=14, microseconds=-1)
    assert store.context('r') == {'schema_version': 1, 'empty': b'', 'deleted': None}
    now[0] += timedelta(microseconds=1)
    with pytest.raises(ContextExpired):
        store.context('r')
    sdk.fail = lambda q, p: True
    with pytest.raises(StorageError):
        store.context('r')


@pytest.mark.parametrize('mode', ['doc_translate', 'doc_verify', 'doc_continue'])
def test_attempt_and_final_save_idempotency_all_modes(db, mode):
    store, sdk, now = db
    run = run_store(store, mode)
    records = tuple(attempt(now[0], str(i + 1) + '.125', op, id=op)
                    for i, op in enumerate(('translation', 'critic', 'repair')))
    for a in records:
        run.record_request(a.request)
        run.record_request(a.request)
        run.record_attempt(a)
        run.record_attempt(a)
    result = RunResult(mode=mode, attempts=records, cancelled=True, errors=('cancelled',))
    for _ in range(2):
        finalize(result, run.hooks())
    assert store.daily_cost() == Decimal('6.375')
    data = context_with_records(store, run.run_id)
    assert data['cost_breakdown'] == dict(translation=Decimal('1.125'), critic=Decimal('2.125'),
                                         repair=Decimal('3.125'), total=Decimal('6.375'))
    assert len(sdk.ledger) == 4
    assert data['result']['cancelled'] and data['result']['errors'] == ['cancelled']
    assert len(data['requests']) == len(data['attempts']) == 3


def test_late_request_replay_must_not_erase_known_paid_cost(db):
    store, _, now = db
    run = run_store(store)
    a = attempt(now[0], '7.25')
    run.record_request(a.request)
    run.record_attempt(a)
    run.record_request(a.request)
    assert store.daily_cost() == Decimal('7.25')


def test_one_admission_whole_run_no_reservations_next_run_blocked(db):
    store, sdk, now = db
    first, parallel = run_store(store), run_store(store)
    first.admit(Decimal(1))
    parallel.admit(Decimal(1))
    for op in ('translation', 'critic', 'repair'):
        first.record_attempt(attempt(now[0], '2', op, id=op))
        first.admit(Decimal(1))
    assert sum('FROM runs' in q for q, _ in sdk.calls) == 2
    assert store.daily_cost() == 6
    with pytest.raises(BudgetExceeded, match='YDBDOC_DAILY_BUDGET_RUB'):
        run_store(store).admit(Decimal(6))


def test_moscow_calendar_boundary_and_pagination(db):
    store, _, now = db
    now[0] = datetime(2026, 1, 31, 20, 59, 59, 999999, tzinfo=UTC)
    run_store(store).record_attempt(attempt(now[0], '5'))
    assert store.daily_cost() == 5
    now[0] += timedelta(microseconds=1)
    assert store.daily_cost() == 0
    for i in range(1001):
        store.ledger(f'r{i:04}', 'paid', created=now[0], mode='doc_continue',
                     status='failed', cost=Decimal('0.001'))
    assert store.daily_cost() == Decimal('1.001')


@pytest.mark.parametrize('failure', ['exhausted', 'outage'])
def test_admission_failure_cached_and_persisted_without_paid_attempt(db, failure):
    store, sdk, now = db
    if failure == 'exhausted':
        run_store(store).record_attempt(attempt(now[0], '1'))
        error = BudgetExceeded
    else:
        sdk.fail = lambda q, p: 'FROM runs' in q
        error = StorageError
    run = run_store(store)
    for _ in range(2):
        with pytest.raises(error) as caught:
            run.admit(Decimal(1))
    assert sum('FROM runs' in q for q, _ in sdk.calls) == 1
    result = finalize(RunResult(errors=(str(caught.value),)), run.hooks())
    data = context_with_records(store, run.run_id)
    assert data['result']['errors'] == list(result.errors)
    assert data['cost_breakdown']['total'] == 0 and not data['attempts']


@pytest.mark.parametrize('status', ['NO_WORK', 'GREEN'])
def test_unpaid_finalize_never_queries_budget(db, status):
    store, sdk, _ = db
    sdk.fail = lambda q, p: 'FROM runs' in q
    run = run_store(store)
    assert finalize(RunResult(status=status), run.hooks()).status == status
    assert context_with_records(store, run.run_id)['cost_breakdown']['total'] == 0


@pytest.mark.parametrize('phase', ['request_chunk', 'pending_ledger'])
def test_before_request_storage_failure_no_http_no_phantom_paid_attempt(db, monkeypatch, phase):
    store, sdk, _ = db
    calls = transport(monkeypatch)
    sdk.fail = lambda q, p: ('UPSERT INTO run_objects' in q if phase == 'request_chunk'
                             else 'UPSERT INTO runs' in q)
    run = run_store(store)
    client = run.model_factory()
    with pytest.raises(StorageError) as caught:
        chat(client)
    assert calls == [] and client.attempts == []
    sdk.fail = lambda q, p: False
    finalize(RunResult(errors=(str(caught.value),)), run.hooks())
    data = context_with_records(store, run.run_id)
    assert data['cost_breakdown']['total'] == 0
    assert data['result']['errors'] and data['requests']
    assert store.daily_cost() == 0
    client.close()


@pytest.mark.parametrize('phase', ['attempt_ledger', 'attempt_object'])
def test_paid_call_storage_failure_reconciles_with_exact_response(db, monkeypatch, phase):
    store, sdk, _ = db
    def fail_after_http(count):
        sdk.fail = lambda q, p: ('UPSERT INTO runs' in q if phase == 'attempt_ledger' else
                                'UPSERT INTO run_objects' in q and p['object_key'].startswith('attempt/'))
    calls = transport(monkeypatch, after=fail_after_http)
    run = run_store(store)
    client = run.model_factory(cost_resolver=lambda e, r: Decimal('1.23456789'))
    with pytest.raises(StorageError) as caught:
        chat(client)
    assert len(calls) == len(client.attempts) == 1
    assert client.cost_breakdown()['total'] == Decimal('1.23456789')
    if phase == 'attempt_object':
        assert store.daily_cost() == Decimal('1.23456789')
    sdk.fail = lambda q, p: False
    finalize(RunResult(attempts=tuple(client.attempts), errors=(str(caught.value),)), run.hooks())
    data = context_with_records(store, run.run_id)
    assert data['attempts'][0]['response_text'] == client.attempts[0].response_text
    assert data['result']['errors'] and data['cost_breakdown']['total'] == Decimal('1.23456789')
    assert store.daily_cost() == Decimal('1.23456789')
    client.close()


def test_paid_fallback_and_failed_calls_are_all_counted(db, monkeypatch):
    store, _, _ = db
    calls = transport(monkeypatch, (500, 200, 400))
    run = run_store(store)
    client = run.model_factory(cost_resolver=lambda e, r: Decimal('.375'))
    assert chat(client).content == 'final'
    with pytest.raises(ModelError):
        chat(client, 'repair')
    run.save(RunResult(attempts=tuple(client.attempts), errors=('HTTP 400',)))
    data = context_with_records(store, run.run_id)
    assert len(calls) == len(data['requests']) == len(data['attempts']) == 3
    assert data['cost_breakdown'] == dict(translation=Decimal('.750'), critic=Decimal(0),
                                         repair=Decimal('.375'), total=Decimal('1.125'))
    assert store.daily_cost() == Decimal('1.125')
    client.close()


@pytest.mark.parametrize('kind', ['price_missing', 'usage_missing', 'zero'])
def test_unknown_price_usage_and_explicit_zero_are_distinct(db, monkeypatch, kind):
    store, _, _ = db
    transport(monkeypatch, missing_usage=kind == 'usage_missing')
    run = run_store(store)
    def resolver(endpoint, response):
        if kind == 'zero':
            return Decimal(0)  # Explicit fixture billing, not a default tariff.
        return rub_resolver()(endpoint, response)
    client = run.model_factory(cost_resolver=resolver)
    chat(client)
    run.save(RunResult(attempts=tuple(client.attempts)))
    data = context_with_records(store, run.run_id)
    assert data['cost_breakdown']['total'] == (Decimal(0) if kind == 'zero' else None)
    assert (data['attempts'][0]['usage']['raw'] is None) == (kind == 'usage_missing')
    if kind != 'zero':
        with pytest.raises(BudgetUnknown):
            run_store(store).admit(Decimal(10))
    client.close()


def test_complete_context_reads_real_git_candidate_without_creating_commit(db):
    store, _, now = db
    repo = Path(__file__).resolve().parents[2]
    sha = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    candidate = Candidate.open(repo, sha)
    path = 'REQUIREMENTS_RU.md'
    assert candidate.read(path)
    run = run_store(store, continuation_count=2)
    a = attempt(now[0])
    result = RunResult(snapshot=Snapshot('o', 'r', 1, sha, sha, 'topic', 'o/r'),
                       candidate=candidate, checked_sha=sha,
                       publication=Publication('o/r', 'branch', 'topic', sha, head_confirmed=True),
                       files=(FileResult(path, 'initial text, not final bytes', (), False),),
                       selected_files=(SelectedFile(path, 'full original', 'en', 'instruction',
                                                    (('one', 'two'),)),),
                       issues=(Issue(path, 'problem', 'repair suggestion'),),
                       unfinished_files=(path,), attempts=(a,), cancelled=True)
    run.save(result)
    data = context_with_records(store, run.run_id)
    assert data['source_sha'] == data['result_sha'] == data['candidate_sha'] == sha
    assert data['final_files'][path] == candidate.read(path)
    assert data['known_files'][0]['source'] == 'full original'
    assert data['known_files'][0]['instruction'] == 'instruction'
    assert data['known_files'][0]['glossary'] == [['one', 'two']]
    assert data['result']['issues'][0]['problem'] == 'problem'
    assert data['result']['unfinished_files'] == [path]
    assert data['requests'][0]['payload'] == a.request.payload
    assert data['attempts'][0]['usage']['raw'] == a.usage.raw
    assert data['attempts'][0]['response_text'] == a.response_text
    assert data['continuation_count'] == 2


def test_primary_paid_alternative_request_failure_preserves_primary(db, monkeypatch):
    store, sdk, _ = db
    def after(count):
        sdk.fail = lambda q, p: ('UPSERT INTO run_objects' in q
                                and p['object_key'].startswith('request/')
                                and p['object_key'].endswith('/1'))
    calls = transport(monkeypatch, (500,), after=after)
    run = run_store(store)
    client = run.model_factory(cost_resolver=lambda e, r: Decimal('2.125'))
    with pytest.raises(StorageError) as caught:
        chat(client)
    assert len(calls) == len(client.attempts) == 1
    sdk.fail = lambda q, p: False
    run.save(RunResult(attempts=tuple(client.attempts), errors=(str(caught.value),)))
    data = context_with_records(store, run.run_id)
    assert len(data['requests']) == 2 and len(data['attempts']) == 1
    assert data['attempts'][0]['status_code'] == 500
    assert data['result']['errors']
    assert data['cost_breakdown']['total'] == store.daily_cost() == Decimal('2.125')
    client.close()


@pytest.mark.parametrize('error', [KeyboardInterrupt, RuntimeError])
def test_interrupted_inflight_request_is_unknown_and_kept(db, monkeypatch, error):
    store, _, _ = db
    def send(*args, **kwargs):
        raise error('transport interrupted')
    monkeypatch.setattr(requests.Session, 'send', send)
    run = run_store(store)
    client = run.model_factory()
    with pytest.raises(error):
        chat(client)
    result = RunResult(cancelled=error is KeyboardInterrupt, attempts=tuple(client.attempts),
                       errors=('transport interrupted',))
    run.save(result)
    data = context_with_records(store, run.run_id)
    assert len(data['attempts']) == 1
    assert data['attempts'][0]['response_text'] is None
    assert data['attempts'][0]['error']
    assert data['cost_breakdown']['total'] is None and store.daily_cost() is None
    client.close()


def test_actual_runner_no_work_bypasses_broken_budget(db, monkeypatch):
    from ydbdoc_review.config.loader import Settings
    from ydbdoc_review.document import RequestBudget
    from ydbdoc_review.github.client import GitHubClient
    from ydbdoc_review.publication import Publisher
    from ydbdoc_review.runner import run_translate

    store, sdk, _ = db
    sdk.fail = lambda q, p: 'FROM runs' in q
    run = run_store(store)
    calls = []
    def send(session, request, **kwargs):
        calls.append(request.url)
        assert request.method == 'GET'
        response = requests.Response()
        response.status_code = 200
        body = ([] if request.url.split('?')[0].endswith('/files') else
                dict(state='open', merged=False, head=dict(sha='a'*40, ref='topic',
                     repo=dict(full_name='o/r')), base=dict(ref='main')))
        response._content = json.dumps(body).encode()
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    github = GitHubClient('dummy')
    publisher = Publisher(github, 'o/r', 'https://github.com/o/r.git', 'translation', 'dummy')
    choice = ModelChoice(Endpoint('eliza', 'https://invalid.test', 'main', 'dummy'))
    result = run_translate(repo=Path(__file__).resolve().parents[2], github=github, owner='o',
                           repository='r', pr_number=1, actor='writer',
                           settings=Settings(20, 250000, frozenset({'writer'}), Decimal(0),
                                             'endpoint', 'db', 'dummy'), publisher=publisher,
                           model_factory=lambda: pytest.fail('no-work constructed model'),
                           admit=lambda: run.admit(Decimal(0)), translation_choice=choice,
                           critic_choice=choice, repair_choice=choice,
                           budget=RequestBudget(10000, 1000, lambda m: len(str(m))), hooks=run.hooks())
    assert result.status == 'NO_WORK', result.errors
    assert len(calls) == 2
    assert not any('FROM runs' in q for q, _ in sdk.calls)
    assert context_with_records(store, run.run_id)['cost_breakdown']['total'] == 0
