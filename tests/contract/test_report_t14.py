"""Real rendering, HTTP comments, RunStore and all three new runners, offline."""
# ruff: noqa: F811, RUF001 -- pytest fixtures / intentional source language.
import json
from dataclasses import replace
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
import requests

from tests.contract.test_continue_t13 import continued  # noqa: F401
from tests.contract.test_translate_t10 import ROOT
from tests.contract.test_translate_t10 import system as translate_system  # noqa: F401
from tests.contract.test_verify_t11 import GOOD, english_source, system  # noqa: F401
from tests.unit.test_store_t12 import db  # noqa: F401
from ydbdoc_review.document import FileResult
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.links import Candidate
from ydbdoc_review.plan import Plan, PlanOperation, Snapshot
from ydbdoc_review.publication import Publication, freeze
from ydbdoc_review.quality import Issue, Location
from ydbdoc_review.quality_loop import SelectedFile
from ydbdoc_review.report import create_reporter, render_reports, report_hooks
from ydbdoc_review.runner import RunHooks, RunResult, finalize
from ydbdoc_review.store import RunStore, decode

pytestmark = pytest.mark.timeout(120)


def capture(monkeypatch, *, fail_at=None, after=None):
    old = requests.Session.send
    sent = []
    def send(session, req, **kw):
        if req.method == 'POST' and urlsplit(req.url).path.endswith('/comments'):
            sent.append((req.url, json.loads(req.body)['body']))
            r = requests.Response()
            r.status_code = 503 if len(sent) == fail_at else 201
            r._content = json.dumps({'html_url': 'https://github.com/up/docs/comment/3'}).encode()
            if after:
                after()
            return r
        return old(session, req, **kw)
    monkeypatch.setattr(requests.Session, 'send', send)
    return sent


@pytest.fixture
def located(git_repo):
    repo, git = git_repo
    base = Candidate.open(repo, git('rev-parse', 'HEAD').decode().strip())
    source = '# Исходник\r\n\r\nПотерянный блок.\r\n'
    original = freeze(base, {'ydb/docs/ru/a.md': source.encode()})
    final = freeze(original, {ROOT+'en/a.md': b'# Final\r\n\r\nFinal excerpt.\r\n'})
    snapshot = Snapshot('up', 'docs', 1, original.sha, original.sha, 'topic', 'up/docs')
    plan = Plan(snapshot)
    plan.files[ROOT+'ru/a.md'] = source
    plan.operations.append(PlanOperation('added', 'ru', 'en', ROOT+'ru/a.md', ROOT+'ru/a.md',
                                         ROOT+'en/a.md', ROOT+'en/a.md', True))
    return RunResult(status='RED', snapshot=snapshot, plan=plan, candidate=final,
        checked_sha=final.sha, publication=Publication('up/docs', 'translation', 'topic', final.sha,
                                                      2, head_confirmed=True),
        files=(FileResult(ROOT+'en/a.md', 'STALE_INITIAL_TRANSCRIPT', (), False),),
        selected_files=(SelectedFile(ROOT+'en/a.md', source, 'en', 'SECRET_INSTRUCTION'),),
        issues=(Issue(ROOT+'en/a.md', 'Missing block', 'Insert missing source paragraph here',
                      target=Location(3, 3, 'Final excerpt.'), source=Location(3, 3, 'Потерянный блок.')),),
        cost_breakdown=dict(translation=Decimal('1.123456789'), critic=Decimal('2.30'),
                            repair=Decimal('0.000000001'), total=Decimal('3.423456790')))


def test_actual_candidate_bytes_locations_costs_and_true_pr(located):
    comments = render_reports(located, current_pr='up/docs/1')
    assert [c.pr for c in comments] == ['up/docs/1', 'up/docs/2']
    brief, detail = [c.body for c in comments]
    assert 'https://github.com/up/docs/pull/2' in brief
    assert f'/blob/{located.candidate_sha}/ydb/docs/en/a.md#L3' in detail
    assert f'/blob/{located.snapshot.source_sha}/ydb/docs/ru/a.md#L3' in detail
    assert '> Final excerpt.' in detail and '> Потерянный блок.' in detail
    for body in (brief, detail):
        assert 'Итого: 3.423456790 ₽' in body
        assert 'Перевод: 1.123456789 ₽' in body
        assert 'Критик: 2.30 ₽' in body and 'Исправления: 0.000000001 ₽' in body
        assert 'STALE_INITIAL_TRANSCRIPT' not in body and 'SECRET_INSTRUCTION' not in body
        assert 'RED — мержить нельзя' in body


@pytest.mark.parametrize('case', ['unconfirmed', 'no_publication', 'stale_quote', 'unlocalized', 'missing_file',
                                  'unverified_candidate', 'continue_source'])
def test_honest_locations_and_checked_sha(located, case):
    result = located
    if case == 'unconfirmed':
        result = replace(result, publication=replace(result.publication, head_confirmed=False))
    elif case == 'no_publication':
        result = replace(result, mode='doc_verify', publication=None)
    elif case == 'stale_quote':
        result = replace(result, issues=(replace(result.issues[0], target=Location(3, 3, 'OLD TEXT')),))
    elif case == 'unlocalized':
        result = replace(result, issues=(Issue(ROOT+'en/a.md', 'paragraph counts 2 versus 1', 'Locate omission'),))
    elif case == 'missing_file':
        result = replace(result, issues=(Issue(ROOT+'en/missing.md', 'File missing', 'Add file'),))
    elif case == 'unverified_candidate':
        result = replace(result, checked_sha=result.snapshot.source_sha, status='GREEN')
    elif case == 'continue_source':
        result = replace(result, mode='doc_continue', plan=None)
    comments = render_reports(result, current_pr='up/docs/2')
    detail = comments[-1].body
    if case in {'unconfirmed', 'no_publication'}:
        assert 'Опубликованный SHA не подтверждён' in detail
        assert '/en/a.md#L3' not in detail
    elif case in {'stale_quote', 'unlocalized', 'missing_file'}:
        assert 'место не установлено' in detail
        assert '/en/a.md#L3' not in detail and 'OLD TEXT' not in detail
        if case == 'missing_file':
            assert 'Файл отсутствует' in detail
    elif case == 'unverified_candidate':
        assert f'SHA последнего проверенного коммита: {result.checked_sha}' in detail
        assert f'Кандидат: {result.candidate_sha} — этот SHA не проверен' in detail
        assert 'GREEN' not in detail
    else:
        assert comments[0].pr == 'up/docs/1'
        assert '> Потерянный блок.' in detail


@pytest.mark.parametrize('mode', ['doc_translate', 'doc_verify', 'doc_continue'])
def test_zero_unknown_security_and_no_fake_pr(mode):
    result = RunResult(mode=mode, message='failure token=abc Bearer xyz https://user:pass@host/x ghp_fakekey',
                       errors=('HTTP 500 {"prompt": "FULL_TRANSCRIPT"}',),
                       cost_breakdown=dict(translation=Decimal(0), critic=None, repair=Decimal(0), total=None))
    comments = render_reports(result, current_pr='up/docs/1')
    assert len(comments) == 1 and comments[0].pr == 'up/docs/1'
    body = comments[0].body
    assert 'Перевод: 0 ₽' in body and 'Итого: неизвестно (RUB)' in body
    for secret in ('abc', 'xyz', 'user:pass', 'ghp_fakekey', 'FULL_TRANSCRIPT'):
        assert secret not in body


def test_adapter_authorization_no_http_by_default(monkeypatch):
    sent = capture(monkeypatch)
    report = create_reporter(GitHubClient('dummy'), current_pr='up/docs/1')
    assert not sent
    with pytest.raises(PermissionError):
        report(RunResult())
    assert not sent


def wired(system, db, monkeypatch, **capture_options):
    state, _, _, _, _, _, publisher, _ = system
    store, _, _ = db
    adapter = RunStore(store, mode='doc_verify' if publisher.pr_number else 'doc_translate', source_pr='up/docs/1')
    sent = capture(monkeypatch, **capture_options)
    def factory():
        client = adapter.model_factory(cost_resolver=lambda e, data: Decimal('.25'))
        def record(request):
            state['records'].append(request)
            adapter.record_request(request)
        client.record_request = record
        return client
    hooks = report_hooks(adapter, publisher.github, current_pr='up/docs/1', authorized=True,
                         cancelled=lambda: state['cancelled'])
    return adapter, sent, dict(model_factory=factory, admit=lambda: adapter.admit(Decimal(100)), hooks=hooks)


@pytest.mark.parametrize('case', ['green', 'cancel', 'no_work', 'publish_failure', 'report_failure',
                                  'storage_failure', 'late_cancel', 'limit', 'acl', 'budget'])
def test_full_translate_report_and_state(translate_system, db, monkeypatch, case):
    state, run, _, _, _, remote_sha, _, settings = translate_system
    extra = {'fail_at': 2} if case == 'report_failure' else {}
    if case == 'late_cancel':
        extra['after'] = lambda: state.update(cancelled=True)
    adapter, sent, opts = wired(translate_system, db, monkeypatch, **extra)
    store, boundary, _ = db
    if case == 'cancel':
        def handler(op, data):
            state['cancelled'] = True
            return data.split('\n\n', 1)[1]
        state['handler'] = handler
    elif case == 'no_work':
        state['changes'] = []
    elif case == 'publish_failure':
        state['fail_create'] = True
    elif case == 'storage_failure':
        failed = []
        def fail(query, params):
            if params.get('entry_id') == 'summary' and not failed:
                failed.append(True)
                return True
            return False
        boundary.fail = fail
    elif case == 'limit':
        opts['settings'] = replace(settings, max_source_characters=1)
    elif case == 'acl':
        opts['actor'] = 'outsider'
    elif case == 'budget':
        opts['admit'] = lambda: adapter.admit(Decimal(0))
    result = run(**opts)
    context = store.context(adapter.run_id)
    assert context['result']['status'] == result.status
    assert context['result']['errors'] == list(result.errors)
    assert decode(boundary.runs[adapter.run_id, 'summary']['payload'])['cost_breakdown'] == result.cost_breakdown
    assert context['cost_breakdown'] == result.cost_breakdown
    assert sent
    if case == 'green':
        assert result.status == 'GREEN', result.errors
        assert len(sent) == 2 and '/issues/1/comments' in sent[0][0] and '/issues/2/comments' in sent[1][0]
        assert result.checked_sha == result.result_sha == remote_sha('translation')
        assert context['final_files'][ROOT+'en/a.md'] == result.candidate.read(ROOT+'en/a.md')
        assert all('Итого: 0.50 ₽' in body for _, body in sent)
    elif case == 'no_work':
        assert result.status == 'NO_WORK' and len(sent) == 1
        assert sent[0][1].startswith('Перевод не требуется')
    else:
        assert result.status == 'RED', result.errors
        if case not in {'report_failure', 'late_cancel'}:
            assert all('RED — мержить нельзя' in body for _, body in sent)
        if case in {'report_failure', 'storage_failure', 'late_cancel'}:
            assert result.publication.draft
            assert result.cost_breakdown['total'] == Decimal('.50')
        if case in {'limit', 'acl', 'budget'}:
            assert not state['calls'] and not state['pulls']
            assert result.message in sent[0][1]


def test_full_verify_one_identity_report_and_state(system, db, monkeypatch):
    system[2]({'en/a.md': '# Привет\r\n\r\nHello world.\r\n'})
    system[0]['handler'] = lambda op, data: GOOD if op == 'critic' else english_source(data)
    adapter, sent, opts = wired(system, db, monkeypatch)
    result = system[1](**opts)
    assert result.status == 'GREEN', result.errors
    assert len(sent) == 1 and '/issues/1/comments' in sent[0][0]
    assert 'SHA последнего проверенного' in sent[0][1]
    assert f'{result.checked_sha}' in sent[0][1]
    assert 'Перевод: 0 ₽' in sent[0][1] and 'Итого: 0.75 ₽' in sent[0][1]
    assert not system[0]['pulls']
    context = db[0].context(adapter.run_id)
    assert context['result']['status'] == result.status
    assert context['cost_breakdown'] == result.cost_breakdown
    assert context['final_files'][ROOT+'en/a.md'] == result.candidate.read(ROOT+'en/a.md')


def test_full_continue_original_source_two_reports_state_cost(continued, monkeypatch):
    c = continued
    sent = capture(monkeypatch)
    result = c.run(hooks=RunHooks(report=create_reporter(c.publisher.github, current_pr='up/docs/1',
                                                       authorized=True)))
    assert result.status == 'GREEN', result.errors
    assert len(sent) == 2
    assert '/issues/2/comments' in sent[0][0] and '/issues/1/comments' in sent[1][0]
    assert 'https://github.com/up/docs/pull/1' in sent[0][1]
    assert all('Итого: 0.75 ₽' in body for _, body in sent)
    context = c.store.context(c.adapters[-1].run_id)
    assert context['source_pr'] == 'up/docs/2'
    assert context['result_sha'] == result.checked_sha == c.remote_sha('topic')
    assert context['cost_breakdown'] == result.cost_breakdown
    assert context['final_files'][ROOT+'en/a.md'] == result.candidate.read(ROOT+'en/a.md')
    assert not c.state['pulls']


def test_bounded_finalization_persistent_storage_failure():
    saves, reports = [], []
    def save(result):
        saves.append(result)
        raise OSError('storage unavailable')
    result = finalize(RunResult(status='GREEN'), RunHooks(save=save, report=reports.append))
    assert len(saves) == 2 and len(reports) == 1
    assert result.status == 'RED' and 'storage final outcome' in result.errors[-1]


@pytest.mark.parametrize('mode', ['doc_verify', 'doc_continue'])
def test_full_existing_pr_report_failure_stores_red(system, db, monkeypatch, mode, request):
    if mode == 'doc_continue':
        c = request.getfixturevalue('continued')
        sent = capture(monkeypatch, fail_at=2)
        result = c.run(hooks=RunHooks(report=create_reporter(c.publisher.github, current_pr='up/docs/1',
                                                           authorized=True)))
        context = c.store.context(c.adapters[-1].run_id)
        assert context['source_pr'] == 'up/docs/2'
        assert result.cost_breakdown['total'] == Decimal('.75')
    else:
        adapter, sent, opts = wired(system, db, monkeypatch, fail_at=1)
        result = system[1](**opts)
        context = db[0].context(adapter.run_id)
        assert result.cost_breakdown['total'] == Decimal('.25')
    assert sent and result.status == 'RED' and result.publication.draft
    assert any('report:' in e for e in result.errors)
    assert context['result']['status'] == 'RED'
    assert context['result']['errors'] == list(result.errors)
    assert context['cost_breakdown'] == result.cost_breakdown


def test_http_paid_failure_fallback_in_both_reports_and_ledger(translate_system, db, monkeypatch):
    from ydbdoc_review.model import Endpoint, ModelChoice
    adapter, sent, opts = wired(translate_system, db, monkeypatch)
    old = requests.Session.send
    failed = []
    def send(session, req, **kw):
        if 'model.invalid' in req.url and not failed:
            failed.append(req)
            r = requests.Response()
            r.status_code = 500
            r._content = b'{"error":{"message":"unavailable"},"usage":{"prompt_tokens":10,"completion_tokens":5}}'
            return r
        return old(session, req, **kw)
    monkeypatch.setattr(requests.Session, 'send', send)
    opts['translation_choice'] = ModelChoice(Endpoint('eliza', 'https://model.invalid', 'main', 'dummy'),
                                             Endpoint('eliza', 'https://model.invalid', 'backup', 'dummy'))
    result = translate_system[1](**opts)
    assert result.status == 'GREEN', result.errors
    assert len(result.attempts) == 3 and result.attempts[0].status_code == 500
    assert result.cost_breakdown == dict(translation=Decimal('.50'), critic=Decimal('.25'),
                                         repair=Decimal(0), total=Decimal('.75'))
    assert all('Перевод: 0.50 ₽' in body and 'Итого: 0.75 ₽' in body for _, body in sent)
    assert db[0].context(adapter.run_id)['cost_breakdown'] == result.cost_breakdown
    assert db[0].daily_cost() == Decimal('.75')


@pytest.mark.parametrize('kind', ['dependencies', 'characters', 'budget', 'changed', 'expired'])
def test_exact_denial_messages(kind, located, db):
    from ydbdoc_review.config.loader import Settings
    from ydbdoc_review.plan import PlanLimitError, enforce_limits
    from ydbdoc_review.store import ContextExpired
    if kind in {'dependencies', 'characters'}:
        plan = located.plan
        settings = Settings(0, 1 if kind == 'characters' else 250000, frozenset({'writer'}),
                            Decimal(100), 'endpoint', 'db', 'key')
        if kind == 'dependencies':
            plan.dependencies.append(ROOT+'ru/extra.md')
        with pytest.raises(PlanLimitError) as error:
            enforce_limits(plan, settings)
        message = str(error.value)
        if kind == 'dependencies':
            expected = ('Перевод не запущен: требуется 1 зависимых статей, лимит — 0 '
                        '(`YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE`). Сначала переведите часть '
                        'зависимых статей отдельным PR или увеличьте переменную в настройках '
                        'Actions репозитория ydb-platform/ydb\nydb/docs/ru/extra.md')
        else:
            expected = (f'Перевод не запущен: объём исходных текстов — {len(plan.files[ROOT+"ru/a.md"])} '
                        'символов, лимит — 1 (`YDBDOC_MAX_SOURCE_CHARACTERS`). Разделите изменения '
                        'на несколько PR или увеличьте переменную в настройках Actions репозитория ydb-platform/ydb')
    elif kind == 'budget':
        from ydbdoc_review.store import BudgetExceeded
        adapter = RunStore(db[0], mode='doc_translate', source_pr='up/docs/1')
        with pytest.raises(BudgetExceeded) as error:
            adapter.admit(Decimal(0))
        message = str(error.value)
        expected = ('Запуск отложен: сегодня потрачено 0 ₽, дневной лимит — 0 ₽ '
                    '(YDBDOC_DAILY_BUDGET_RUB). Повторите запуск завтра или увеличьте лимит')
    elif kind == 'expired':
        with pytest.raises(ContextExpired) as error:
            db[0].context('missing')
        message = str(error.value)
        expected = message
        assert '14 дней' in message and 'doc_verify' in message
    else:
        from tests.contract.test_continue_t13 import run_continue
        message = run_continue.__globals__['CHANGED']
        expected = ('После предыдущего запуска появились новые коммиты. Запустите doc_verify '
                    'для проверки текущего перевода или doc_translate для нового перевода')
    assert message == expected
    body = render_reports(RunResult(message=message), current_pr='up/docs/1')[0].body
    assert expected in body


def test_warning_does_not_change_green_and_quote_range(located):
    issue = Issue(ROOT+'en/a.md', 'Кириллица в защищённом коде', 'Проверьте вручную; код не менять',
                  severity='warning', target=Location(1, 3, '# Final\r\n\r\nFinal excerpt.'))
    body = render_reports(replace(located, status='GREEN', issues=(issue,)), current_pr='up/docs/1')[-1].body
    assert body.startswith('GREEN') and 'warning' in body and '#L1-L3' in body
    assert 'YELLOW' not in body


@pytest.mark.parametrize('status,message,cancelled', [
    ('NO_WORK', 'Перевод не требуется', False),
    ('RED', 'Доступ запрещён', False),
    ('RED', 'storage: StorageError: недоступно', False),
    ('RED', 'publication: HTTP 503', False),
    ('RED', '', True),
])
def test_source_report_snapshots(status, message, cancelled):
    result = RunResult(status=status, message=message, cancelled=cancelled)
    verdict = 'Перевод не требуется' if status == 'NO_WORK' else 'RED — мержить нельзя, нужны исправления.'
    parts = [verdict]
    if message and message != verdict:
        parts.append(message)
    if cancelled:
        parts.append('Запуск прерван; сохранена доступная часть результата.')
    parts.extend(['Переводной PR не создан.',
                  '- Перевод: 0 ₽\n- Критик: 0 ₽\n- Исправления: 0 ₽\n- Итого: 0 ₽'])
    assert render_reports(result, current_pr='up/docs/1')[0].body == '\n\n'.join(parts)


def test_production_store_factory_hooks_http(monkeypatch, db):
    from ydbdoc_review import store as storage
    monkeypatch.setattr(storage, 'make_ydb_driver', lambda **kw: db[1])
    store = storage.create_store()
    sent = capture(monkeypatch)
    adapter = RunStore(store, mode='doc_translate', source_pr='up/docs/1')
    result = finalize(RunResult(message='preflight refused'), report_hooks(
        adapter, GitHubClient('dummy'), current_pr='up/docs/1', authorized=True))
    assert store.context(adapter.run_id)['result']['message'] == result.message
    assert len(sent) == 1 and 'preflight refused' in sent[0][1]
    store.close()
    assert db[1].stopped


def test_binary_asset_problem_and_bounded_secret_quote(located):
    candidate = freeze(located.candidate, {ROOT+'en/asset.png': b'\x00\xffbinary'})
    result = replace(located, candidate=candidate, checked_sha=candidate.sha,
                     publication=replace(located.publication, pushed_sha=candidate.sha),
                     issues=(Issue(ROOT+'en/asset.png', 'Invalid asset', 'Replace image'),))
    body = render_reports(result, current_pr='up/docs/1')[-1].body
    assert 'Текст файла недоступен' in body and 'Файл отсутствует' not in body
    text = 'confidential-value ' + 'word ' * 200
    candidate = freeze(candidate, {ROOT+'en/a.md': text.encode()})
    result = replace(result, candidate=candidate, checked_sha=candidate.sha,
                     publication=replace(result.publication, pushed_sha=candidate.sha),
                     issues=(Issue(ROOT+'en/a.md', 'Issue', 'Fix', target=Location(1, 1, text)),))
    body = render_reports(result, current_pr='up/docs/1', secrets=('confidential-value',))[-1].body
    assert 'confidential-value' not in body and 'цитата сокращена' in body
    assert text not in body


def test_continue_after_new_verify_uses_durable_original_identity(continued, monkeypatch):
    from datetime import timedelta
    c = continued
    c.now[0] += timedelta(seconds=1)
    renewed = RunStore(c.store, mode='doc_verify', source_pr='up/docs/1')
    verified = c.verify(model_factory=lambda: c.factory(renewed), hooks=renewed.hooks())
    assert verified.status == 'GREEN'
    context = c.store.latest_context('up/docs/1')
    assert context['source_pr'] == 'up/docs/1' and context['original_pr'] == 'up/docs/2'
    sent = capture(monkeypatch)
    # T13 snapshot now describes the latest verify; durable association belongs
    # to entrypoint metadata and is already available in the store's public API.
    result = c.run(hooks=RunHooks(report=create_reporter(
        c.publisher.github, current_pr='up/docs/1', source_pr=context['original_pr'], authorized=True)))
    assert result.status == 'GREEN', result.errors
    assert len(sent) == 2
    assert '/issues/2/comments' in sent[0][0] and '/issues/1/comments' in sent[1][0]
    assert all('Итого: 0.75 ₽' in body for _, body in sent)
    saved = c.store.context(c.adapters[-1].run_id)
    assert saved['result']['status'] == result.status and saved['cost_breakdown'] == result.cost_breakdown
