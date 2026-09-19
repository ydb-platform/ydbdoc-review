# Independent acceptance scenarios retained in normal CI.
# ruff: noqa: F401,F811 -- imported pytest fixture
"""Independent F11 acceptance: real reporter/runner/client/store, offline boundaries."""
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests

from tests.unit.test_store_t12 import adapter, db, paid
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.publication import Publication
from ydbdoc_review.report import create_reporter
from ydbdoc_review.runner import RunHooks, RunResult, finalize


@pytest.fixture
def transport(monkeypatch):
    state = {'events': [], 'comments': {}, 'description': '', 'failure': None, 'receipt': 101}
    def send(session, request, **kwargs):
        assert urlsplit(request.url).hostname == 'api.github.com', 'No model/live calls allowed'
        path = urlsplit(request.url).path
        data = json.loads(request.body) if request.body else {}
        body = data.get('body', '')
        state['events'].append((request.method, path, body))
        response = requests.Response()
        response.status_code = 201
        payload = {}
        failure = state['failure']
        if failure and failure(request.method, path, body):
            response.status_code = 503
            payload = {'error': 'injected channel failure'}
        elif '/git/' in path:
            payload = {'ref': data['ref'], 'object': {'sha': data['sha']}} if path.endswith('/refs') else {'sha': 'b'*40}
        elif path.endswith('/pulls/2'):
            state['description'] = body
        elif request.method == 'POST':
            receipt = state['receipt']
            state['receipt'] += 7
            state['comments'][receipt] = body
            payload = {'id': state.get('reply_id', receipt)}
        else:
            receipt = int(path.rsplit('/', 1)[1])
            assert receipt in state['comments'], 'Unconfirmed comment ID'
            state['comments'][receipt] = body
        response._content = json.dumps(payload).encode()
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    return state


def result(mode):
    return RunResult(mode=mode, status='GREEN', checked_sha='a'*40,
                     candidate=SimpleNamespace(sha='a'*40),
                     publication=Publication('o/r', 'translation', 'main', 'a'*40, 2,
                                             draft=True, head_confirmed=True),
                     cost_breakdown={k: Decimal('3.25') for k in ('translation', 'critic', 'repair', 'total')})


def reporter():
    return create_reporter(GitHubClient('offline-token'), current_pr='o/r/2',
                           source_pr='o/r/1', authorized=True)


@pytest.mark.parametrize('mode', ['doc_translate', 'doc_verify', 'doc_continue'])
@pytest.mark.parametrize('failure', ['none', 'first_early', 'second_early', 'first_late', 'second_late', 'storage', 'late_storage', 'description'])
def test_delivery_matrix(mode, failure, transport):
    full_saves, metadata_saves = [], []
    def inject(verb, path, body):
        if failure == 'description':
            return path.endswith('/pulls/2')
        if failure == 'first_early':
            return verb == 'POST' and '/issues/1/comments' in path
        if failure == 'second_early':
            return verb == 'POST' and '/issues/2/comments' in path
        return False
    transport['failure'] = inject
    def save(value):
        full_saves.append(value)
        # Both comment channels and description were attempted before context IO.
        paths = [e[1] for e in transport['events']]
        assert '/repos/o/r/issues/1/comments' in paths
        assert '/repos/o/r/issues/2/comments' in paths
        assert '/repos/o/r/pulls/2' in paths
        for body in [*transport['comments'].values(), transport['description']]:
            if body:
                assert 'GREEN' not in body and 'Итого: 3.25 ₽' in body
        if failure == 'storage':
            raise OSError('storage unavailable')
        if failure in ('first_late', 'second_late', 'late_storage'):
            ident = 101 if failure == 'first_late' else 108
            transport['failure'] = lambda verb, path, body: path.endswith(f'/comments/{ident}')
    def save_status(value):
        metadata_saves.append(value)
        if failure == 'late_storage':
            raise OSError('metadata unavailable')
    actual = finalize(result(mode), RunHooks(save=save, save_status=save_status, report=reporter()))
    assert len(full_saves) == 1
    assert actual.cost_breakdown == result(mode).cost_breakdown
    assert actual.status == ('GREEN' if failure == 'none' else 'RED')
    assert len([e for e in transport['events'] if e[0] == 'POST' and '/issues/' in e[1]]) == 2
    bodies = [*transport['comments'].values(), transport['description']]
    assert all('GREEN' not in body for body in bodies) if failure != 'none' else all('GREEN' in b for b in bodies)
    if failure in ('storage', 'late_storage'):
        healthy = [body for body in bodies if 'doc_continue недоступен' in body]
        assert healthy, 'Storage failure must visibly explain unavailable continuation: ' + repr(bodies)


def test_long_storage_diagnostic_keeps_continue_action(transport):
    def save(value):
        raise OSError('Transport RPC diagnostics: ' + 'x'*1000)
    actual = finalize(result('doc_translate'), RunHooks(save=save, report=reporter()))
    assert actual.status == 'RED'
    assert all('doc_continue недоступен' in body for body in [*transport['comments'].values(), transport['description']])
    assert all('doc_verify' in body for body in [*transport['comments'].values(), transport['description']])


def test_metadata_only_and_cost_once_real_store(db):
    store, boundary, _ = db
    run = adapter(store)
    attempt = paid(cost='7.125')
    result_value = RunResult(attempts=(attempt,), cost_breakdown={k: Decimal('7.125') for k in ('translation', 'critic', 'repair', 'total')})
    calls = []
    full_save = run.save
    def save(value):
        calls.append(value)
        full_save(value)
    def report(value):
        boundary.calls.clear()
        raise OSError('report rejected')
    actual = finalize(result_value, replace(run.hooks(report=report), save=save))
    assert actual.status == 'RED' and len(calls) == 1
    keys = {p['object_key'] for q, p in boundary.calls if 'UPSERT INTO run_objects' in q}
    assert keys == {'context'}
    assert store.daily_cost() == Decimal('7.125')
    assert len([v for v in boundary.runs.values() if v['entry_id'] != 'summary']) == 1


@pytest.mark.parametrize('bad_id', [None, 0, -8, True, '101', {}, []])
def test_invalid_creation_receipts_are_never_guessed(bad_id, transport):
    transport['reply_id'] = bad_id
    actual = finalize(result('doc_translate'), RunHooks(save=lambda value: None, report=reporter()))
    assert actual.status == 'RED'
    assert not [e for e in transport['events'] if e[0] == 'PATCH' and '/issues/comments/' in e[1]]
    assert len([e for e in transport['events'] if e[0] == 'POST' and '/issues/' in e[1]]) == 2
    assert all('GREEN' not in body for body in transport['comments'].values())
