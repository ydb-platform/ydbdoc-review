"""Offline HTTP regressions for cancellation and pre-admission capabilities."""
import json
from collections import Counter
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests

from ydbdoc_review.cli import execute
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.publication import Publication
from ydbdoc_review.report import create_reporter
from ydbdoc_review.runner import RunHooks, RunResult, finalize


@pytest.fixture
def http(monkeypatch):
    state = {'calls': [], 'counts': Counter(), 'fault': lambda verb, path, count: None,
             'next_id': 101, 'comments': {}}
    def send(session, request, **kwargs):
        assert urlsplit(request.url).hostname == 'api.github.com', 'No model calls allowed'
        path = urlsplit(request.url).path
        data = json.loads(request.body)
        state['calls'].append((request.method, path, data))
        state['counts'][request.method, path] += 1
        fault = state['fault'](request.method, path, state['counts'][request.method, path])
        if fault:
            raise fault
        payload = {}
        if '/git/' in path:
            payload = ({'ref': data['ref'], 'object': {'sha': data['sha']}}
                       if path.endswith('/refs') else {'sha': 'b'*40})
        elif path.endswith('/pulls/2'):
            state['description'] = data['body']
        elif request.method == 'POST':
            ident = state['next_id']
            state['next_id'] += 1
            state['comments'][ident] = data['body']
            payload = {'id': ident}
        else:
            ident = int(path.rsplit('/', 1)[1])
            assert ident in state['comments']
            state['comments'][ident] = data['body']
        response = requests.Response()
        response.status_code = 201
        response._content = json.dumps(payload).encode()
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    return state


@pytest.mark.parametrize('mode', ['doc_translate', 'doc_verify', 'doc_continue'])
@pytest.mark.parametrize('reason', ['settings', 'acl', 'runtime'])
def test_cli_before_admission_only_posts_refusal(http, monkeypatch, tmp_path, mode, reason):
    values = dict(GITHUB_TOKEN='offline', GITHUB_ACTOR='writer', YDB_SA_KEY='offline',
                  YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE='20', YDBDOC_MAX_SOURCE_CHARACTERS='250000',
                  YDBDOC_ALLOWED_ACTORS='writer', YDBDOC_DAILY_BUDGET_RUB='100')
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    if reason == 'settings':
        monkeypatch.delenv('YDBDOC_MAX_SOURCE_CHARACTERS')
    if reason == 'acl':
        monkeypatch.setenv('GITHUB_ACTOR', 'outsider')
    result = execute(mode, 'up/docs', 1, config=tmp_path/'missing.json')
    assert result.status == 'RED' and not result.attempts
    assert [(verb, path) for verb, path, _ in http['calls']] == [
        ('POST', '/repos/up/docs/issues/1/comments')]
    assert result.message and 'Итого: 0 ₽' in http['comments'][101]


@pytest.mark.parametrize('boundary', ['early_source', 'late_source', 'description', 'artifact', 'reconciliation'])
def test_interrupt_retains_cancellation_and_attempts_other_channels(http, boundary):
    def fault(verb, path, count):
        match = ((boundary == 'early_source' and verb == 'POST' and path.endswith('/issues/1/comments')) or
                 (boundary == 'late_source' and verb == 'PATCH' and path.endswith('/comments/101') and count == 1) or
                 (boundary == 'description' and path.endswith('/pulls/2') and count == 1) or
                 (boundary == 'artifact' and path.endswith('/blobs')) or
                 (boundary == 'reconciliation' and path.endswith('/comments/102') and count == 2))
        if match:
            return KeyboardInterrupt('report interrupted')
        if boundary == 'reconciliation' and path.endswith('/comments/102') and count == 1:
            return RuntimeError('later channel failed')
    http['fault'] = fault
    result = RunResult(mode='doc_verify', status='GREEN', checked_sha='a'*40,
                       candidate=SimpleNamespace(sha='a'*40),
                       publication=Publication('up/docs', 'translation', 'main', 'a'*40, 2,
                                               draft=True, head_confirmed=True),
                       message='diagnostic '*100 if boundary == 'artifact' else '',
                       cost_breakdown={k: Decimal('2') for k in ('translation', 'critic', 'repair', 'total')})
    saves, statuses = [], []
    reporter = create_reporter(GitHubClient('offline'), current_pr='up/docs/2',
                               source_pr='up/docs/1', authorized=True)
    actual = finalize(result, RunHooks(save=saves.append, save_status=statuses.append, report=reporter))
    assert actual.status == 'RED' and actual.cancelled
    assert len(saves) == 1 and actual.cost_breakdown == result.cost_breakdown
    persisted = statuses[-1] if statuses else saves[-1]
    assert persisted.cancelled and persisted.status == 'RED'
    assert len([1 for verb, path, _ in http['calls'] if verb == 'POST' and '/issues/' in path]) == 2
    assert any(path.endswith('/pulls/2') for _, path, _ in http['calls'])
    assert all('GREEN' not in body for body in http['comments'].values())
