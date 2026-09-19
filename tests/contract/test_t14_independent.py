"""T14 independent acceptance: real render/HTTP adapter/store/finalize, offline.

Only reusable infrastructure fixtures are imported. No T14 developer test helpers.
T15 may relocate these imports, but must preserve the observable assertions.
"""
# ruff: noqa: F811, F401, RUF001
import base64
import json
import re
from dataclasses import replace
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest
import requests
from markdown_it import MarkdownIt

from tests.contract.test_continue_t13 import continued, request_greeting
from tests.contract.test_translate_t10 import system as translate_system
from tests.contract.test_verify_t11 import english_source, system
from tests.model_clock import model_clock
from tests.unit.test_store_t12 import db
from ydbdoc_review.document import FileResult
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.links import Candidate
from ydbdoc_review.plan import Plan, PlanOperation, Snapshot
from ydbdoc_review.publication import Publication, freeze
from ydbdoc_review.quality import Issue, Location
from ydbdoc_review.quality_loop import SelectedFile
from ydbdoc_review.report import create_reporter, render_reports, report_hooks
from ydbdoc_review.runner import RunHooks, RunResult, finalize
from ydbdoc_review.store import RunStore

ROOT = 'ydb/docs/'
GOOD = json.dumps(dict(complete=True, verdict='correct', issues=[]))
pytestmark = pytest.mark.timeout(60)


def http_comments(monkeypatch, fail_at=None, after=None):
    previous = requests.Session.send
    class Captured(list):
        def __init__(self):
            super().__init__()
            self.requests, self.objects, self.refs = [], {}, {}
            self.bodies, self.full_saves = [], []

        def artifacts(self):
            decoded = []
            for body in self.bodies:
                for sha in re.findall(r'/blob/([0-9a-f]{40})/diagnostics.json', body):
                    assert sha in self.refs.values()
                    kind, commit = self.objects[sha]
                    assert kind == 'commits'
                    kind, tree = self.objects[commit['tree']]
                    assert kind == 'trees'
                    entry, = tree['tree']
                    assert entry['path'] == 'diagnostics.json' and entry['type'] == 'blob'
                    kind, blob = self.objects[entry['sha']]
                    assert kind == 'blobs' and blob['encoding'] == 'base64'
                    decoded.append(json.loads(base64.b64decode(blob['content'], validate=True)))
            return decoded

    captured = Captured()
    def send(session, request, **kwargs):
        path = urlsplit(request.url).path
        if urlsplit(request.url).hostname != 'api.github.com':
            return previous(session, request, **kwargs)
        data = json.loads(request.body) if request.body else {}
        response = requests.Response()
        response.status_code = 201
        if request.method == 'POST' and path.endswith('/comments'):
            captured.append((request.url, data['body']))
            captured.bodies.append(data['body'])
            payload = {'id': len(captured)}
            if after:
                after()
        elif request.method == 'PATCH' and '/issues/comments/' in path:
            ident = int(path.rsplit('/', 1)[-1])
            assert 0 < ident <= len(captured), 'unconfirmed comment ID'
            captured.bodies.append(data['body'])
            # Failure cases target final PATCH delivery; progress gets a real ID.
            if ident == fail_at:
                response.status_code = 503
            else:
                captured[ident - 1] = (captured[ident - 1][0], data['body'])
            payload = {'id': ident}
        elif request.method == 'POST' and '/git/' in path:
            kind = path.rsplit('/', 1)[-1]
            if kind == 'refs':
                assert captured.objects[data['sha']][0] == 'commits'
                captured.refs[data['ref']] = data['sha']
                payload = {'ref': data['ref'], 'object': {'sha': data['sha']}}
            else:
                assert kind in ('blobs', 'trees', 'commits'), request.url
                sha = f'{len(captured.objects) + 1:040x}'
                captured.objects[sha] = (kind, data)
                payload = {'sha': sha}
        elif request.method == 'PATCH' and '/pulls/' in path:
            captured.bodies.append(data['body'])
            payload = {}
        else:
            # All non-report endpoints remain governed by the existing strict
            # runner HTTP boundary; this adapter never invents their success.
            return previous(session, request, **kwargs)
        captured.requests.append(request)
        response._content = json.dumps(payload).encode()
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    return captured



def wire(fixture, db, monkeypatch, **http_options):
    model_clock(monkeypatch, db[2])
    state, _, _, _, _, _, publisher, _ = fixture
    adapter = RunStore(db[0], mode='doc_verify' if publisher.pr_number else 'doc_translate',
                       source_pr='up/docs/1')
    captured = http_comments(monkeypatch, **http_options)
    def factory():
        state['made'] += 1
        client = adapter.model_factory(cost_resolver=lambda endpoint, data: Decimal('.125'))
        def record(req):
            state['records'].append(req)
            adapter.record_request(req)
        client.record_request = record
        return client
    hooks = report_hooks(adapter, publisher.github, current_pr='up/docs/1', authorized=True,
                         cancelled=lambda: state['cancelled'])
    def save_once(result):
        captured.full_saves.append(result)
        adapter.save(result)
    hooks = replace(hooks, save=save_once)
    return adapter, captured, dict(model_factory=factory, admit=lambda: adapter.admit(Decimal(100)), hooks=hooks)


@pytest.mark.parametrize('case', ['green', 'report_first', 'report_second', 'late_cancel', 'push', 'storage_once', 'resave_fails'])
def test_real_finalization_cost_and_delivery(translate_system, db, monkeypatch, case):
    state, run, _, _, _, remote_sha, _, _ = translate_system
    options = {'fail_at': 1 if case == 'report_first' else 2} if case.startswith('report_') or case == 'resave_fails' else {}
    if case == 'late_cancel':
        options['after'] = lambda: state.update(cancelled=True)
    adapter, sent, args = wire(translate_system, db, monkeypatch, **options)
    if case == 'push':
        hook = state['remote'] / 'hooks/pre-receive'
        hook.parent.mkdir(exist_ok=True)
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
    summaries = []
    def fail(query, params):
        if 'UPSERT INTO runs' in query and params.get('entry_id') == 'summary':
            summaries.append(params)
            return (case == 'storage_once' and len(summaries) == 1) or (case == 'resave_fails' and len(summaries) == 2)
        return False
    db[1].fail = fail
    result = run(**args)
    assert [op for op, _ in state['calls']] == ['translation', 'critic'], (result.errors, result.files, result.attempts)
    assert state['made'] == 1 and len(result.attempts) == 2
    assert result.cost_breakdown['total'] == db[0].daily_cost() == Decimal('.250')
    assert len([k for k in db[1].runs if k[1] != 'summary']) == 2
    assert len(captured_saves := sent.full_saves) == 1
    assert captured_saves[0].attempts == result.attempts
    assert len(summaries) <= 2
    assert len(sent) == (1 if case == 'push' else 2)
    saved = db[1].runs[adapter.run_id, 'summary']
    if case == 'resave_fails':
        assert result.status == 'RED'
        assert any('storage status' in error for error in result.errors)
        assert saved['status'] == 'GREEN'  # unavailable resave is honestly reported, not claimed successful
    else:
        context = db[0].context(adapter.run_id)
        assert saved['status'] == context['result']['status'] == result.status
        assert context['result']['errors'] == list(result.errors)
        assert context['cost_breakdown'] == result.cost_breakdown
    if case == 'green':
        assert result.status == 'GREEN'
        assert result.result_sha == result.checked_sha == remote_sha('translation')
        assert sent[0][0].endswith('/issues/1/comments') and sent[1][0].endswith('/issues/2/comments')
        assert 'SHA последнего проверенного' not in sent[0][1]
        assert result.checked_sha in sent[1][1]
    else:
        assert result.status == 'RED'
        if case != 'push':
            assert result.publication.draft and result.result_sha == remote_sha('translation')
        else:
            assert result.result_sha is None and remote_sha('translation') is None
            assert 'Переводной PR не создан' in sent[0][1]
    for _, body in sent:
        assert all(label in body for label in ('Перевод: 0.125 ₽', 'Критик: 0.125 ₽', 'Исправления: 0 ₽', 'Итого: 0.250 ₽'))


@pytest.mark.parametrize('case', ['acl', 'characters', 'dependencies', 'budget', 'no_work'])
def test_real_preflight_report_without_model_or_acl_bypass(translate_system, db, monkeypatch, case):
    state, run, commit, _, _, remote_sha, _, settings = translate_system
    adapter, sent, args = wire(translate_system, db, monkeypatch)
    if case == 'acl':
        args['actor'] = 'outsider'
        expected = 'YDBDOC_ALLOWED_ACTORS'
    elif case == 'characters':
        args['settings'] = replace(settings, max_source_characters=1)
        expected = ('Перевод не запущен: объём исходных текстов — 22 символов, лимит — 1 '
                    '(`YDBDOC_MAX_SOURCE_CHARACTERS`). Разделите изменения на несколько PR или увеличьте '
                    'переменную в настройках Actions репозитория ydb-platform/ydb')
    elif case == 'dependencies':
        commit({'ru/a.md': '# A\n\n[B](b.md)\n', 'ru/b.md': '# B\n'})
        args['settings'] = replace(settings, max_dependency_files=0)
        expected = ('Перевод не запущен: требуется 1 зависимых статей, лимит — 0 '
                    '(`YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE`). Сначала переведите часть зависимых '
                    'статей отдельным PR или увеличьте переменную в настройках Actions репозитория '
                    'ydb-platform/ydb\nydb/docs/ru/b.md')
    elif case == 'budget':
        args['admit'] = lambda: adapter.admit(Decimal(0))
        expected = ('Запуск отложен: сегодня потрачено 0 ₽, дневной лимит — 0 ₽ '
                    '(YDBDOC_DAILY_BUDGET_RUB). Повторите запуск завтра или увеличьте лимит')
    else:
        state['changes'] = []
        expected = 'Перевод не требуется'
    result = run(**args)
    assert result.status == ('NO_WORK' if case == 'no_work' else 'RED')
    assert not state['calls'] and state['made'] == 0 and not state['pulls']
    assert remote_sha('translation') is None and result.candidate is None
    assert len(sent) == 1 and expected in sent[0][1]
    assert not any('report:' in error for error in result.errors)
    assert db[0].context(adapter.run_id)['result']['status'] == result.status


def test_verify_repairs_en_only_reports_current_pr(system, db, monkeypatch):
    state, run, commit, _, _, remote_sha, *_ = system
    commit({'en/a.md': '# Привет\n\nHello world.\n'})
    state['handler'] = lambda op, data: GOOD if op == 'critic' else english_source(data)
    adapter, sent, args = wire(system, db, monkeypatch)
    result = run(**args)
    assert result.status == 'GREEN', result.errors
    assert [op for op, _ in state['calls']] == ['critic', 'repair', 'critic']
    assert result.candidate.text(ROOT+'ru/a.md') == '# Привет\n\nПривет, мир.\n'
    assert result.checked_sha == result.result_sha == remote_sha('topic')
    assert len(sent) == 1 and sent[0][0].endswith('/issues/1/comments')
    assert result.checked_sha in sent[0][1] and 'Перевод: 0 ₽' in sent[0][1]
    assert not state['pulls']
    assert db[0].context(adapter.run_id)['final_files'][ROOT+'en/a.md'] == result.candidate.read(ROOT+'en/a.md')


def test_continue_original_source_reports(continued, monkeypatch):
    c = continued
    request_greeting(c, 'Hello, world.')
    sent = http_comments(monkeypatch)
    result = c.run(hooks=RunHooks(report=create_reporter(c.publisher.github, current_pr='up/docs/1', authorized=True)))
    assert result.status == 'GREEN', result.errors
    assert len(sent) == 2 and sent[0][0].endswith('/issues/2/comments') and sent[1][0].endswith('/issues/1/comments')
    assert 'https://github.com/up/docs/pull/1' in sent[0][1]
    assert 'SHA последнего проверенного' not in sent[0][1]
    assert result.checked_sha == result.result_sha == c.remote_sha('topic')
    assert [op for op, _ in c.state['calls']] == ['critic', 'repair', 'critic']
    assert all('Итого: 0.75 ₽' in body for _, body in sent)
    assert c.store.context(c.adapters[-1].run_id)['source_pr'] == 'up/docs/2'


@pytest.fixture
def evidence(git_repo):
    repo, git = git_repo
    base = Candidate.open(repo, git('rev-parse', 'HEAD').decode().strip())
    source = '# Source\n\nMissing paragraph.\n'
    original = freeze(base, {ROOT+'ru/a.md': source.encode()})
    final = freeze(original, {ROOT+'en/a.md': b'# Repaired\n\nFinal paragraph.\n'})
    snapshot = Snapshot('up', 'docs', 1, original.sha, original.sha, 'topic', 'up/docs')
    plan = Plan(snapshot)
    plan.files[ROOT+'ru/a.md'] = source
    plan.operations.append(PlanOperation('added', 'ru', 'en', ROOT+'ru/a.md', ROOT+'ru/a.md', ROOT+'en/a.md', ROOT+'en/a.md', True))
    return RunResult(snapshot=snapshot, plan=plan, candidate=final, checked_sha=final.sha,
        publication=Publication('up/docs', 'translation', 'topic', final.sha, 2, head_confirmed=True),
        files=(FileResult(ROOT+'en/a.md', 'INITIAL_NOT_FINAL', (), False),),
        selected_files=(SelectedFile(ROOT+'en/a.md', source, 'en', ''),),
        issues=(Issue(ROOT+'en/a.md', 'Missing paragraph', 'Insert before final paragraph',
                      target=Location(3, 3, 'Final paragraph.'), source=Location(3, 3, 'Missing paragraph.')),))


@pytest.mark.parametrize('variant', ['valid', 'stale', 'unlocalized', 'unconfirmed', 'warning', 'unknown'])
def test_render_actual_bytes_and_honesty(evidence, variant):
    result = evidence
    if variant == 'stale':
        result = replace(result, issues=(replace(result.issues[0], target=Location(3, 3, 'INITIAL_NOT_FINAL')),))
    elif variant == 'unlocalized':
        result = replace(result, issues=(Issue(ROOT+'en/a.md', 'Structure count differs', 'Find missing block'),))
    elif variant == 'unconfirmed':
        result = replace(result, publication=replace(result.publication, head_confirmed=False))
    elif variant == 'warning':
        result = replace(result, status='GREEN', issues=(replace(result.issues[0], severity='warning', problem='Кириллица в защищённом коде'),))
    elif variant == 'unknown':
        result = replace(result, cost_breakdown={'translation': Decimal(0), 'critic': None, 'repair': Decimal('1.230000001'), 'total': None})
    brief, detail = [c.body for c in render_reports(result, current_pr='up/docs/1')]
    assert 'INITIAL_NOT_FINAL' not in detail
    assert ('GREEN' if variant == 'warning' else 'RED') in detail
    assert 'YELLOW' not in detail
    if variant in {'valid', 'warning', 'unknown'}:
        assert f'/blob/{result.result_sha}/{ROOT}en/a.md#L3' in detail
        assert '> Final paragraph.' in detail and '> Missing paragraph.' in detail
        assert f'/blob/{result.snapshot.source_sha}/{ROOT}ru/a.md#L3' in detail
        assert 'Основные причины и действия:' in brief
        assert result.issues[0].problem in brief
        assert '> Final paragraph.' not in brief and '> Missing paragraph.' not in brief
    else:
        assert '/en/a.md#L3' not in detail
        assert 'не установлено' in detail
    if variant == 'unknown':
        for body in (brief, detail):
            assert 'Итого: неизвестно (RUB)' in body and 'Критик: неизвестно (RUB)' in body
            assert 'Перевод: 0 ₽' in body and 'Исправления: 1.230000001 ₽' in body


class VisibleQuote(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag == 'blockquote':
            self.depth += 1
    def handle_endtag(self, tag):
        if tag == 'blockquote':
            self.depth -= 1
    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


@pytest.mark.parametrize('quote', ['<script>alert("x")</script>', '&lt;node&gt; &amp; value', '[visible](https://example.invalid) **bold** `code`', '```\nquoted code\n```'])
def test_external_quote_visible_text_preserves_candidate(evidence, quote):
    candidate = freeze(evidence.candidate, {ROOT+'en/a.md': quote.encode()})
    result = replace(evidence, candidate=candidate, checked_sha=candidate.sha,
        publication=replace(evidence.publication, pushed_sha=candidate.sha),
        issues=(Issue(ROOT+'en/a.md', 'External excerpt', 'Review excerpt', target=Location(1, len(quote.splitlines()), quote)),))
    body = render_reports(result, current_pr='up/docs/1')[-1].body
    html = MarkdownIt('commonmark', {'html': True}).render(body)
    assert '<script>' not in html
    visible = VisibleQuote()
    visible.feed(html)
    assert ''.join(visible.parts).strip() == quote
    assert 'Итого:' in html  # quote cannot swallow report footer


def test_partial_cancel_reports_unfinished(translate_system, db, monkeypatch):
    state, run, commit, *_ = translate_system
    commit({'ru/b.md': '# Second\n'})
    state['changes'].append(dict(filename=ROOT+'ru/b.md', status='added'))
    adapter, sent, args = wire(translate_system, db, monkeypatch)
    hooks = args['hooks']
    def progress(file):
        hooks.file_progress(file)
        state['cancelled'] = True
    args['hooks'] = replace(hooks, file_progress=progress)
    result = run(**args)
    assert result.cancelled and result.status == 'RED' and result.publication.draft
    assert len(state['calls']) == 1
    assert result.candidate.read(ROOT+'en/a.md') and ROOT+'en/b.md' in result.unfinished_files
    assert all('Незавершённые файлы: ydb/docs/en/b.md' in body for _, body in sent)
    assert all('Запуск прерван' in body for _, body in sent)
    assert db[0].context(adapter.run_id)['result']['cancelled']


@pytest.mark.parametrize('case', ['changed_source', 'changed_target', 'storage', 'acl'])
def test_continue_denial_reporting(continued, monkeypatch, case):
    c = continued
    sent = http_comments(monkeypatch)
    args = dict(hooks=RunHooks(report=create_reporter(c.publisher.github, current_pr='up/docs/1', authorized=True)))
    if case == 'changed_source':
        c.state['source_head'] = '0' * 40
    elif case == 'changed_target':
        c.commit({'unrelated.md': 'unrelated new commit'})
    elif case == 'storage':
        c.boundary.fail = lambda query, params: True
    else:
        args['actor'] = 'outsider'
    result = c.run(**args)
    assert result.status == 'RED' and not c.state['calls'] and c.state['made'] == 0
    assert sent and all('RED' in body for _, body in sent)
    text = '\n'.join(body for _, body in sent)
    if case.startswith('changed'):
        assert ('После предыдущего запуска появились новые коммиты. Запустите doc_verify для '
                'проверки текущего перевода или doc_translate для нового перевода') in text
    elif case == 'storage':
        assert 'StorageError' in text and '14 дней' not in text
    else:
        assert 'YDBDOC_ALLOWED_ACTORS' in text
    assert not any('report:' in error for error in result.errors)


def test_permission_flag_cannot_silently_drop_denial_report(monkeypatch, db):
    sent = http_comments(monkeypatch)
    adapter = RunStore(db[0], mode='doc_translate', source_pr='up/docs/1')
    # This is a transport permission, independent of denied product actor access.
    denied = RunResult(message='Доступ запрещён: actor отсутствует в YDBDOC_ALLOWED_ACTORS')
    result = finalize(denied, report_hooks(adapter, GitHubClient('dummy'), current_pr='up/docs/1', authorized=True))
    assert result is denied and len(sent) == 1 and denied.message in sent[0][1]
    assert db[0].context(adapter.run_id)['result']['status'] == 'RED'


def test_paid_http_failure_fallback_and_report_failure_not_double_billed(translate_system, db, monkeypatch):
    from ydbdoc_review.model import Endpoint, ModelChoice
    _, run, *_ = translate_system
    adapter, sent, args = wire(translate_system, db, monkeypatch, fail_at=2)
    previous = requests.Session.send
    model_http = []
    def send(session, request, **kwargs):
        if 'model.invalid' in request.url:
            model_http.append(request)
            if len(model_http) == 1:
                response = requests.Response()
                response.status_code = 503
                response._content = b'{"usage":{"prompt_tokens":9,"completion_tokens":1},"error":{"message":"unavailable"}}'
                return response
        return previous(session, request, **kwargs)
    monkeypatch.setattr(requests.Session, 'send', send)
    args['translation_choice'] = ModelChoice(Endpoint('eliza', 'https://model.invalid', 'main', 'dummy'),
                                            Endpoint('eliza', 'https://model.invalid', 'backup', 'dummy'))
    result = run(**args)
    assert result.status == 'RED' and result.publication.draft
    assert len(model_http) == len(result.attempts) == 3
    assert result.attempts[0].status_code == 503
    assert db[0].daily_cost() == result.cost_breakdown['total'] == Decimal('.375')
    assert all('Перевод: 0.250 ₽' in body and 'Итого: 0.375 ₽' in body for _, body in sent)
    assert db[0].context(adapter.run_id)['result']['status'] == 'RED'


def test_real_protected_code_warning_keeps_green(translate_system, db, monkeypatch):
    _, run, commit, *_ = translate_system
    commit({'ru/a.md': '# Code\n\n```python\nname = "Привет"\n```\n'})
    _, sent, args = wire(translate_system, db, monkeypatch)
    result = run(**args)
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert any(issue.severity == 'warning' for issue in result.issues)
    assert result.candidate.text(ROOT+'en/a.md') == '# Code\n\n```python\nname = "Привет"\n```\n'
    assert sent[-1][1].startswith('GREEN') and 'warning' in sent[-1][1]
    assert 'YELLOW' not in sent[-1][1]


@pytest.mark.parametrize('failure', ['report', 'storage_once', 'resave_fails'])
def test_verify_finalization_reconciles_real_store_without_extra_paid_attempt(system, db, monkeypatch, failure):
    state, run, *_ = system
    adapter, sent, args = wire(system, db, monkeypatch, fail_at=1 if failure != 'storage_once' else None)
    summaries = []
    def fail(query, params):
        if 'UPSERT INTO runs' in query and params.get('entry_id') == 'summary':
            summaries.append(params)
            return (failure == 'storage_once' and len(summaries) == 1) or (failure == 'resave_fails' and len(summaries) == 2)
        return False
    db[1].fail = fail
    result = run(**args)
    assert result.status == 'RED' and result.publication.draft, result.errors
    assert [op for op, _ in state['calls']] == ['critic']
    assert len(result.attempts) == 1 and state['made'] == 1 and len(sent) == 1
    assert db[0].daily_cost() == result.cost_breakdown['total'] == Decimal('.125')
    assert len([key for key in db[1].runs if key[1] != 'summary']) == 1
    assert len(summaries) == 2
    assert len(sent.full_saves) == 1
    if failure == 'resave_fails':
        assert any('storage status' in error for error in result.errors)
        assert db[1].runs[adapter.run_id, 'summary']['status'] == 'GREEN'
    else:
        assert db[1].runs[adapter.run_id, 'summary']['status'] == 'RED'
        context = db[0].context(adapter.run_id)
        assert context['result']['status'] == 'RED' and context['result']['errors'] == list(result.errors)
        assert context['cost_breakdown'] == result.cost_breakdown


def test_continue_after_fresh_verify_keeps_original_source_identity(continued, monkeypatch):
    from datetime import timedelta
    c = continued
    request_greeting(c, 'Hello, world.')
    c.now[0] += timedelta(seconds=1)
    adapter = RunStore(c.store, mode='doc_verify', source_pr='up/docs/1')
    verified = c.verify(model_factory=lambda: c.factory(adapter), hooks=adapter.hooks())
    assert verified.status == 'GREEN'
    context = c.store.latest_context('up/docs/1')
    assert context['original_pr'] == 'up/docs/2'
    sent = http_comments(monkeypatch)
    result = c.run(hooks=RunHooks(report=create_reporter(c.publisher.github, current_pr='up/docs/1',
                     source_pr=context['original_pr'], authorized=True)))
    assert result.status == 'GREEN', result.errors
    assert len(sent) == 2 and sent[0][0].endswith('/issues/2/comments') and sent[1][0].endswith('/issues/1/comments')
    assert 'https://github.com/up/docs/pull/1' in sent[0][1]
    assert result.result_sha in sent[1][1]
    assert c.store.context(c.adapters[-1].run_id)['cost_breakdown'] == result.cost_breakdown


def test_verify_acl_denial_still_reports_with_transport_permission(system, db, monkeypatch):
    state, run, *_ = system
    _, sent, args = wire(system, db, monkeypatch)
    result = run(actor='outsider', **args)
    assert result.status == 'RED' and not state['calls'] and state['made'] == 0
    assert not state['pulls'] and result.candidate is None
    assert len(sent) == 1 and sent[0][0].endswith('/issues/1/comments')
    assert 'YDBDOC_ALLOWED_ACTORS' in sent[0][1]
    assert not any('report:' in error for error in result.errors)
