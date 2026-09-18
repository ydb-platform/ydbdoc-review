"""Incident-scale diagnostics at requests' real prepared HTTP boundary, no network."""
# ruff: noqa: F811 -- pytest fixtures
import base64
import json
import subprocess
from dataclasses import replace
from urllib.parse import urlsplit

import pytest
import requests

from tests.contract.test_report_t14 import located  # noqa: F401
from tests.contract.test_translate_t10 import system as translate_system  # noqa: F401
from ydbdoc_review.build import BuildResult
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.links import Candidate, LinkResult, UncheckedAnchor
from ydbdoc_review.quality import Issue, Location
from ydbdoc_review.quality_loop import LoopResult, RoundTrace
from ydbdoc_review.report import (
    ReportDeliveryError,
    create_reporter,
    initial_description,
    render_reports,
)


@pytest.fixture
def boundary(monkeypatch):
    calls = []
    state = {'fail': None, 'artifact': None}
    def send(session, request, **kwargs):
        assert request.url.startswith('https://api.github.com/')
        path = urlsplit(request.url).path
        data = json.loads(request.body)
        calls.append((request.method, path, data))
        response = requests.Response()
        response.status_code = 201
        if 'body' in data:
            assert len(data['body']) < 12001
        if state['fail'] and state['fail'] in path:
            response.status_code = 422
            payload = {'message': 'upload rejected token=NEVER_PRINT'}
        elif path.endswith('/blobs'):
            state['artifact'] = json.loads(base64.b64decode(data['content']))
            payload = {'sha': 'a' * 40}
        elif path.endswith('/refs'):
            payload = {'ref': data['ref'], 'object': {'sha': data['sha']}}
        else:
            payload = {'sha': 'a' * 40, 'html_url': 'https://github.com/up/docs/comment/1'}
        response._content = json.dumps(payload).encode()
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    return state, calls


def large(located):
    return replace(located, issues=tuple(
        Issue(f'ydb/docs/en/page-{i % 1148}.md', 'Anchor unchecked because build failed',
              'Complete the build, then check anchors', 'unchecked_anchor', target=Location(3, 3, 'quote'))
        for i in range(8894)))


def test_incident_8894_small_bodies_complete_artifact_immutable_candidate(located, boundary):
    state, calls = boundary
    result = large(located)
    before = result.candidate_sha
    create_reporter(GitHubClient('dummy'), current_pr='up/docs/1', authorized=True)(result)
    bodies = [data['body'] for _, _, data in calls if 'body' in data]
    assert len(bodies) == 3
    for body in bodies:
        assert len(body) < 6000
        assert '8894' in body and '1148' in body
        assert 'Итого: 3.423456790 ₽' in body
        assert 'diagnostics.json' in body
        assert 'Anchor unchecked' in body
    assert len(state['artifact']['issues']) == 8894
    assert state['artifact']['issues'][-1]['target'] == {'start': 3, 'end': 3, 'quote': 'quote'}
    assert state['artifact']['issues'][-1]['path'].endswith('page-857.md')
    commit = next(data for _, path, data in calls if path.endswith('/commits'))
    ref = next(data for _, path, data in calls if path.endswith('/refs'))
    assert commit['parents'] == []
    assert ref['ref'].startswith('refs/heads/ydbdoc-reports/')
    assert result.candidate_sha == before
    assert not any(method in {'PUT', 'DELETE'} for method, _, _ in calls)


@pytest.mark.parametrize('failure', ['/blobs', '/refs', '/issues/1/comments', '/issues/2/comments'])
def test_failure_does_not_skip_other_channels_or_costs(located, boundary, failure):
    state, calls = boundary
    state['fail'] = failure
    result = replace(located, status='GREEN')
    with pytest.raises(ReportDeliveryError) as error:
        create_reporter(GitHubClient('dummy'), current_pr='up/docs/1', authorized=True)(result)
    assert 'NEVER_PRINT' not in str(error.value)
    bodies = [(path, data['body']) for _, path, data in calls if 'body' in data]
    assert len(bodies) == 3
    assert '/issues/1/comments' in bodies[0][0]
    assert '/issues/2/comments' in bodies[1][0]
    assert '/pulls/2' in bodies[2][0]
    assert all('Итого: 3.423456790 ₽' in body for _, body in bodies)
    assert 'GREEN' not in bodies[-1][1]
    if failure in {'/blobs', '/refs'}:
        assert all('Артефакт не опубликован' in body and 'diagnostics.json' not in body for _, body in bodies)


def test_initial_partial_cost_and_pending_check(located):
    result = replace(located, publication=None, checked_sha=None, status='GREEN',
                     unfinished_files=('ydb/docs/en/a.md',))
    body = initial_description(result)
    assert 'GREEN' not in body
    assert 'Незавершённые файлы' in body
    assert 'этот SHA не проверен' in body
    assert 'Итого: 3.423456790 ₽' in body
    assert 'публикации' in body


def test_long_errors_paths_are_bounded_without_losing_cost_or_sha(located):
    result = replace(located, errors=('x' * 100000,), issues=(Issue('z' * 100000, 'p' * 100000, 'f' * 100000),))
    bodies = render_reports(result, current_pr='up/docs/1')
    assert all(len(c.body) <= 12000 for c in bodies)
    assert located.checked_sha in bodies[-1].body
    assert all('Итого: 3.423456790 ₽' in c.body for c in bodies)


def test_f08_full_build_logs_and_unchecked_anchors(located, boundary):
    state, _ = boundary
    sha = located.candidate_sha
    # F08 separates dependent anchor diagnostics from root build issues.
    links = LinkResult(sha, (), False, (UncheckedAnchor('ydb/docs/en/a.md', 'other.md#missing', 'ydb/docs/en/other.md', 'ydb/docs/en/a.md', Location(3, 3, 'quote')),))
    build = BuildResult(sha, 'failure', 'start\nprivate-value\n' + 'technical details\n' * 1000, 1)
    trace = RoundTrace(1, sha, (), build, links, located.issues)
    result = replace(located, quality=LoopResult(located.candidate, 'RED', sha, located.issues, (), (trace,)))
    create_reporter(GitHubClient('dummy'), current_pr='up/docs/1', authorized=True,
                    secrets=('private-value',))(result)
    artifact = state['artifact']
    assert 'private-value' not in json.dumps(artifact)
    assert artifact['rounds'][0]['build']['log'].count('technical details') == 1000
    assert len(artifact['rounds'][0]['links']['unchecked_anchors']) == 1


def test_creation_http_body_contains_partial_result_and_current_cost(translate_system):
    state, run, *_ = translate_system
    state['handler'] = lambda op, data: '# Partial' if op == 'translation' else '{"verdict": "correct"}'
    result = run()
    body = state['pulls'][0][1]['body']
    assert 'RED — мержить нельзя' in body
    assert 'Обработанные файлы' in body or 'Незавершённые файлы' in body
    assert 'Итого:' in body and 'Перевод:' in body
    assert 'SHA' in body
    assert result.cost_breakdown['total'] > 0


def test_stale_green_label_cannot_hide_errors_or_partial_files(located):
    for result in (replace(located, status='GREEN'),
                   replace(located, status='GREEN', issues=(), unfinished_files=('a.md',))):
        assert all('GREEN' not in c.body for c in render_reports(result, current_pr='up/docs/1'))


@pytest.mark.parametrize('path', ['../../outside.md', '/absolute.md', r'bad\path.md'])
def test_malformed_issue_path_preserves_all_delivery_channels(located, boundary, path):
    state, calls = boundary
    result = replace(located, issues=(replace(located.issues[0], path=path),))
    create_reporter(GitHubClient('dummy'), current_pr='up/docs/1', authorized=True)(result)
    bodies = [data['body'] for _, _, data in calls if 'body' in data]
    assert len(bodies) == 3
    assert all('RED — мержить нельзя' in body and 'Итого: 3.423456790 ₽' in body for body in bodies)
    assert 'Текст файла недоступен; строки перевода не установлены' in bodies[-1]
    assert '/blob/' not in bodies[-1].replace('https://github.com/up/docs/blob/' + 'a' * 40 + '/diagnostics.json', '')
    assert state['artifact']['issues'][0]['path'] == path


@pytest.mark.parametrize('source', [False, True])
@pytest.mark.parametrize('error', [ValueError('invalid path'), OSError('unreadable snapshot'),
                                   RuntimeError('git object unavailable'), subprocess.TimeoutExpired('git', 60)])
def test_unreadable_location_preserves_channels_cost_and_honest_coordinates(
        located, boundary, monkeypatch, source, error):
    _, calls = boundary
    result = replace(located, plan=None) if source else located
    def fail(*args, **kwargs):
        raise error
    if source:
        monkeypatch.setattr(Candidate, 'open', fail)
    else:
        monkeypatch.setattr(Candidate, 'read', fail)
    create_reporter(GitHubClient('dummy'), current_pr='up/docs/1', authorized=True)(result)
    bodies = [data['body'] for _, _, data in calls if 'body' in data]
    assert len(bodies) == 3
    assert all('Итого: 3.423456790 ₽' in body for body in bodies)
    assert ('Исходник недоступен' if source else 'Текст файла недоступен') in bodies[-1]
    assert ('/ru/a.md#' if source else '/en/a.md#') not in bodies[-1]
