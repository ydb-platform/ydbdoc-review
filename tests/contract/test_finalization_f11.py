"""Finalization boundaries: all HTTP is intercepted, no model or live storage."""
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests

from tests.unit.test_store_t12 import db  # noqa: F401

from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.report import create_reporter
from ydbdoc_review.runner import RunHooks, RunResult, finalize


@pytest.fixture
def delivery(monkeypatch):
    events = []
    state = {'fail': None, 'missing_id': False, 'comments': {}}
    def send(session, request, **kwargs):
        path = urlsplit(request.url).path
        data = json.loads(request.body)
        if '/git/' in path:
            response = requests.Response()
            response.status_code = 201
            payload = ({'ref': data['ref'], 'object': {'sha': data['sha']}}
                       if path.endswith('/refs') else {'sha': 'b'*40})
            response._content = json.dumps(payload).encode()
            return response
        body = data['body']
        events.append((request.method, path, body))
        response = requests.Response()
        response.status_code = 201
        payload = {}
        if state['fail'] == path:
            response.status_code = 503
        elif request.method == 'POST':
            ident = len(state['comments']) + 10
            state['comments'][ident] = body
            if not state['missing_id']:
                payload['id'] = ident
        else:
            ident = int(path.rsplit('/', 1)[1])
            assert ident in state['comments'], 'Only confirmed creation IDs may be updated'
            state['comments'][ident] = body
        response._content = json.dumps(payload).encode()
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    reporter = create_reporter(GitHubClient('dummy'), current_pr='up/docs/2',
                               source_pr='up/docs/1', authorized=True)
    return reporter, state, events


def ready():
    return RunResult(mode='doc_verify', status='GREEN', checked_sha='a'*40,
                     candidate=SimpleNamespace(sha='a'*40),
                     cost_breakdown={k: Decimal('2') for k in ('translation', 'critic', 'repair', 'total')})


def test_report_and_cost_precede_context_and_updates_use_receipts(delivery):
    reporter, state, events = delivery
    saves = []
    def save(result):
        assert len(events) == 2
        assert all('GREEN' not in e[2] and 'Итого: 2 ₽' in e[2] for e in events)
        saves.append(result)
    result = finalize(ready(), RunHooks(save=save, report=reporter))
    assert result.status == 'GREEN'
    assert len(saves) == 1
    assert [e[0] for e in events] == ['POST', 'POST', 'PATCH', 'PATCH']
    assert all('GREEN' in body for body in state['comments'].values())


@pytest.mark.parametrize('channel', ['/repos/up/docs/issues/1/comments',
                                   '/repos/up/docs/issues/2/comments'])
def test_first_or_second_report_failure_never_replays_context(delivery, channel):
    reporter, state, events = delivery
    state['fail'] = channel
    saves, statuses = [], []
    result = finalize(ready(), RunHooks(save=saves.append, save_status=statuses.append, report=reporter))
    assert result.status == 'RED'
    assert len(saves) == 1
    assert [e[1] for e in events[:2]] == ['/repos/up/docs/issues/1/comments',
                                             '/repos/up/docs/issues/2/comments']
    assert len([e for e in events if e[0] == 'POST']) == 2
    assert all('GREEN' not in body and 'Итого: 2 ₽' in body for body in state['comments'].values())


@pytest.mark.parametrize('diagnostic', ['storage offline', 'RPC diagnostics: ' + 'x'*1000])
def test_storage_failure_reports_continue_unavailability_without_retry(delivery, diagnostic):
    reporter, state, _ = delivery
    calls, statuses = [], []
    def fail(result):
        calls.append(result)
        raise RuntimeError(diagnostic)
    result = finalize(ready(), RunHooks(save=fail, save_status=statuses.append, report=reporter))
    assert len(calls) == 1
    assert len(statuses) == 1
    assert result.status == 'RED'
    assert all('doc_continue недоступен' in body and 'Итого: 2 ₽' in body
               and 'GREEN' not in body for body in state['comments'].values())


def test_late_channel_failure_revokes_earlier_green(delivery):
    reporter, state, events = delivery
    saves, statuses = [], []
    def save(result):
        saves.append(result)
        state['fail'] = '/repos/up/docs/issues/comments/11'
    result = finalize(ready(), RunHooks(save=save, save_status=statuses.append, report=reporter))
    assert result.status == 'RED' and len(saves) == 1 and len(statuses) == 1
    assert any('GREEN' in body for method, path, body in events if method == 'PATCH')
    assert all('GREEN' not in body for body in state['comments'].values())


def test_missing_receipt_never_guesses_or_reposts_comment(delivery):
    reporter, state, events = delivery
    state['missing_id'] = True
    result = finalize(ready(), RunHooks(save=lambda r: None, report=reporter))
    assert result.status == 'RED'
    assert len(events) == 2 and all(e[0] == 'POST' for e in events)
    assert all('GREEN' not in body for body in state['comments'].values())


def test_late_metadata_failure_cannot_leave_published_green(delivery):
    reporter, state, _ = delivery
    def save(result):
        state['fail'] = '/repos/up/docs/issues/comments/11'
    def status(result):
        raise RuntimeError('metadata offline')
    result = finalize(ready(), RunHooks(save=save, save_status=status, report=reporter))
    assert result.status == 'RED'
    assert any('storage status' in error for error in result.errors)
    assert 'doc_continue недоступен' in state['comments'][10]
    assert 'doc_verify' in state['comments'][10]
    assert all('GREEN' not in body for body in state['comments'].values())


def test_plain_callback_failure_never_repeats_paid_or_full_save():
    saves, statuses = [], []
    def report(result):
        raise RuntimeError('report offline')
    result = finalize(ready(), RunHooks(save=saves.append, save_status=statuses.append, report=report))
    assert len(saves) == len(statuses) == 1 and result.status == 'RED'
    assert result.cost_breakdown['total'] == Decimal('2')


def test_cost_once_and_metadata_only_after_report_failure(db):  # noqa: F811
    from tests.unit.test_store_t12 import adapter, paid
    from ydbdoc_review.store import decode
    store, boundary, _ = db
    run = adapter(store)
    attempt = paid()
    run.record_request(attempt.request)
    run.record_attempt(attempt)
    writes_before_report = []
    def fail(result):
        writes_before_report.extend(boundary.calls)
        boundary.calls.clear()
        raise RuntimeError('report offline')
    result = finalize(RunResult(attempts=(attempt,)), run.hooks(report=fail))
    assert result.status == 'RED'
    changed = [p['object_key'] for q, p in boundary.calls if 'UPSERT INTO run_objects' in q]
    assert changed and set(changed) == {'context'}
    assert store.daily_cost() == attempt.usage.cost_rub
    assert len([p for p in boundary.runs.values() if p['entry_id'] != 'summary']) == 1
    assert 'report offline' in str(decode(store.get(run.run_id, 'context'))['result']['errors'])



def test_one_context_object_failure_does_not_skip_other_paid_costs(db):  # noqa: F811
    from tests.unit.test_store_t12 import adapter, paid
    store, boundary, _ = db
    run = adapter(store)
    first = paid()
    second = replace(first, request=replace(first.request, id='second'))
    boundary.fail = lambda q, p: ('UPSERT INTO run_objects' in q and
                                 p['object_key'].startswith('attempt/req/'))
    reports = []
    result = finalize(RunResult(attempts=(first, second)), run.hooks(report=reports.append))
    assert result.status == 'RED'
    assert store.daily_cost() == first.usage.cost_rub + second.usage.cost_rub
    assert len([p for p in boundary.runs.values() if p['entry_id'] != 'summary']) == 2
    assert reports and 'doc_continue недоступен' in reports[-1].message


def test_unauthorized_report_reconciliation_never_writes(delivery):
    from ydbdoc_review.publication import Publication
    _, _, events = delivery
    reporter = create_reporter(GitHubClient('dummy'), current_pr='up/docs/2', authorized=False)
    result = replace(ready(), publication=Publication('up/docs', 'translation', 'main', 'a'*40,
                                                      2, draft=True, head_confirmed=True))
    result = finalize(result, RunHooks(save=lambda r: None, report=reporter))
    assert result.status == 'RED' and not events
