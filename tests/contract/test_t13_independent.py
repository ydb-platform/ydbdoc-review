"""Independent component acceptance; only SDK/HTTP boundaries are replaced.

T11 temporary Git/API fixture and T12 relational SQL executor are reused, not
T13's fixture/assertions. Counter YQL is emulated under a transaction lock:
this verifies admission callers, not live YDB's concurrency implementation.
"""
# ruff: noqa: F811, RUF001
import json
import re
import sqlite3
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
import ydb

from tests.contract.test_t12_recheck import PersistentSQL
from tests.contract.test_verify_t11 import GOOD, ROOT, english_source, system  # noqa: F401
from ydbdoc_review.continuation import run_continue as run_continue
from ydbdoc_review.document import RequestBudget
from ydbdoc_review.model import Endpoint, ModelChoice
from ydbdoc_review.publication import freeze
from ydbdoc_review.quality import Issue
from ydbdoc_review.runner import RunHooks
from ydbdoc_review.store import RunStore, YDBStore, encode

pytestmark = pytest.mark.timeout(120)


class SQLBoundary(PersistentSQL):
    def __init__(self, path):
        super().__init__(path)
        self.connection.close()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.gate = None

    def execute(self, sql, params, commit_tx):
        if '$next AS continuation_count' in sql and self.gate:
            self.gate.wait(timeout=15)
        with self.lock:
            if '$next AS continuation_count' not in sql:
                return super().execute(sql, params, commit_tx)
            assert commit_tx
            self.calls.append((sql, dict(params)))
            # Only this YQL multi-statement is emulated; other SQL is executed.
            old = self.connection.execute(
                "SELECT status,payload FROM runs WHERE run_id=? AND entry_id='summary'",
                (params['$key'],)).fetchone()
            count = int(old['status']) if old else 0
            count += not old or old['payload'] != params['$claim']
            if count <= 3:
                self.connection.execute(
                    "INSERT OR REPLACE INTO runs(run_id,entry_id,status,payload,created_at) "
                    "VALUES(?,'summary',?,?,?)",
                    (params['$key'], str(count), params['$claim'], params['$created']))
                self.connection.commit()
            return [SimpleNamespace(rows=[SimpleNamespace(continuation_count=count)])]

    def counter(self):
        return self.connection.execute(
            "SELECT status FROM runs WHERE run_id='continuations/up/docs/2'").fetchone()


@pytest.fixture
def env(system, tmp_path, monkeypatch):
    state, verify, commit, repo, git, remote_sha, publisher, settings = system
    commit({'ru/good.md': '# Хорошо\n\nХорошо.\n',
            'en/good.md': b'# Good\r\n\r\nUNSELECTED_BYTES.\r\n'})
    state['changes'].append(dict(filename=ROOT+'ru/good.md', status='added'))
    sql = SQLBoundary(tmp_path/'t13.sqlite')
    monkeypatch.setattr(ydb, 'SessionPool', lambda driver: sql)
    now = [datetime.now(UTC)]
    store = YDBStore(object(), clock=lambda: now[0])
    source_sha = [state['sha']]
    comments = [dict(body='/ydbdoc continue Improve ydb/docs/en/a.md',
                     created_at=(now[0]-timedelta(minutes=1)).isoformat(),
                     user=dict(login='writer', type='User'))]
    http = []
    transport = requests.Session.send

    def send(session, request, **kwargs):
        http.append((request.method, request.url))
        assert request.method != 'DELETE', 'branch deletion is forbidden'
        path = urlsplit(request.url).path
        data = None
        if path.endswith('/pulls/2'):
            data = dict(state='open', merged=False,
                        head=dict(sha=source_sha[0], ref='source', repo=dict(full_name='up/docs')),
                        base=dict(ref='main'))
        elif '/comments' in path:
            data = comments
        if data is not None:
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(data).encode()
            return response
        return transport(session, request, **kwargs)
    monkeypatch.setattr(requests.Session, 'send', send)
    adapters = []

    def factory(adapter):
        adapters.append(adapter)
        client = adapter.model_factory(cost_resolver=lambda endpoint, data: Decimal('7.25'))
        def record(request):
            state['records'].append(request)
            adapter.record_request(request)
        client.record_request = record
        return client
    first = RunStore(store, mode='doc_verify', source_pr='up/docs/2')
    seeded = verify(model_factory=lambda: factory(first), hooks=first.hooks())
    assert seeded.status == 'GREEN', (seeded.errors, seeded.issues)
    original = store.context(first.run_id)
    adapters.clear()
    state['calls'].clear()
    state['records'].clear()
    state['handler'] = lambda op, data: GOOD if op == 'critic' else english_source(data)
    choice = ModelChoice(Endpoint('eliza', 'https://model.invalid', 'main', 'dummy'))

    def run(**overrides):
        with sql.lock:
            now[0] += timedelta(seconds=1)
        opts = dict(repo=repo, github=publisher.github, owner='up', repository='docs',
                    pr_number=1, actor='writer', settings=replace(settings, daily_budget_rub=Decimal(10000)),
                    publisher=publisher, store=store, critic_choice=choice, repair_choice=choice,
                    budget=RequestBudget(100000, 20000, lambda messages: len(str(messages))),
                    model_factory=factory, hooks=RunHooks(report=state['reported'].append))
        opts.update(overrides)
        return run_continue(**opts)
    yield SimpleNamespace(**locals())
    sql.connection.close()


def request_greeting(e, greeting):
    e.comments[0]['body'] = f'/ydbdoc continue In ydb/docs/en/a.md use greeting "{greeting}"'
    e.state['handler'] = lambda op, data: GOOD if op == 'critic' else re.sub(
        r'Hello,? world[.!]', greeting, english_source(data))


def assert_changed_greeting(result, previous_sha, greeting):
    assert result.candidate_sha != previous_sha
    assert result.candidate.text(ROOT+'en/a.md') == f'# Hello\n\n{greeting}\n'
    assert [r.candidate_sha for r in result.quality.rounds] == [previous_sha, result.checked_sha]


@pytest.mark.parametrize('entry_pr', [1, 2])
def test_lookup_by_pr_new_history_and_minimal_context(env, entry_pr):
    e = env
    old_rows = e.sql.rows()
    old_prompt = encode({'do_not_forward': 'OLD_TRANSCRIPT_SENTINEL' * 1000})
    context = dict(e.original, requests=[{'content': old_prompt}], attempts=[])
    e.store.put(e.first.run_id, 'context', encode(context))
    request_greeting(e, 'Hello, world.')
    result = e.run(pr_number=entry_pr)
    assert_changed_greeting(result, e.seeded.candidate_sha, 'Hello, world.')
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert result.mode == 'doc_continue' and result.files == ()
    assert result.cost_breakdown['translation'] == 0
    assert [op for op, data in e.state['calls']] == ['critic', 'repair', 'critic']
    prompts = json.dumps(e.state['calls'])
    assert 'OLD_TRANSCRIPT_SENTINEL' not in prompts and 'UNSELECTED_BYTES' not in prompts
    assert [f.path for f in result.selected_files] == [ROOT+'en/a.md']
    assert result.candidate.read(ROOT+'en/good.md') == b'# Good\r\n\r\nUNSELECTED_BYTES.\r\n'
    assert result.checked_sha == result.result_sha == e.remote_sha('topic')
    assert result.quality.rounds[-1].build.ok_for(result.result_sha)
    assert result.quality.rounds[-1].links.candidate_sha == result.result_sha
    latest = e.store.latest_context('up/docs/2')
    assert latest['run_id'] == e.adapters[-1].run_id != e.first.run_id
    assert latest['continuation_count'] == 1
    assert e.store.context(e.first.run_id) == context
    assert all(row in e.sql.rows() for row in old_rows)
    assert not e.state['pulls'] and result.publication.pr_number == 1


@pytest.mark.parametrize('mention', ['ydb/docs/en/good.md', 'ydb/docs/ru/good.md', 'good.md'])
def test_explicit_good_file_including_unique_basename(env, mention):
    e = env
    e.comments[0]['body'] = '/ydbdoc continue Improve ' + mention
    e.state['handler'] = lambda op, data: GOOD if op == 'critic' else data['source'].replace('Хорошо', 'Good')
    result = e.run()
    assert result.status == 'GREEN', result.message
    assert [f.path for f in result.selected_files] == [ROOT+'en/good.md']
    assert result.candidate.read(ROOT+'en/a.md') == e.seeded.candidate.read(ROOT+'en/a.md')
    assert [op for op, data in e.state['calls']] == ['critic', 'repair', 'critic']


def test_ambiguous_basename_refuses_without_guessing(env):
    e = env
    extra = replace(e.seeded.selected_files[0], path=ROOT+'en/other/a.md')
    e.first.save(e.seeded, known_files=(*e.seeded.selected_files, extra))
    e.comments[0]['body'] = '/ydbdoc continue Improve a.md'
    result = e.run()
    assert result.status == 'RED' and 'a.md' in result.message
    assert not e.adapters and not e.state['calls'] and e.sql.counter() is None


def test_saved_findings_survive_first_correct_critic(env):
    e = env
    e.first.save(replace(e.seeded, status='RED', issues=(
        Issue(ROOT+'en/a.md', 'SAVED_FINDING_SENTINEL', 'Use greeting Hello, world.'),)))
    request_greeting(e, 'Hello, world.')
    e.comments[0]['body'] = '/ydbdoc continue Resolve saved findings'
    result = e.run()
    assert result.status == 'GREEN', result.message
    assert [op for op, data in e.state['calls']] == ['critic', 'repair', 'critic']
    assert_changed_greeting(result, e.seeded.candidate_sha, 'Hello, world.')
    assert any(f['problem'] == 'SAVED_FINDING_SENTINEL'
               for f in e.state['calls'][1][1]['findings'])


@pytest.mark.parametrize('side', ['source', 'result'])
def test_unrelated_commit_exact_sha_refusal(env, side):
    e = env
    e.git('commit', '--allow-empty', '-m', 'independent unrelated fixture change')
    sha = e.git('rev-parse', 'HEAD').decode().strip()
    if side == 'source':
        e.source_sha[0] = sha
    else:
        e.git('push', 'https://x-access-token:dummy@github.com/up/docs.git', sha+':refs/heads/topic')
    before = e.remote_sha('topic')
    result = e.run()
    expected = ('После предыдущего запуска появились новые коммиты. Запустите doc_verify '
                'для проверки текущего перевода или doc_translate для нового перевода')
    assert result.status == 'RED' and expected in result.message
    assert not e.adapters and not e.state['calls'] and e.sql.counter() is None
    assert e.remote_sha('topic') == before


@pytest.mark.parametrize('reason', ['missing', 'expired', 'outage', 'acl', 'budget'])
def test_refusal_before_model_distinguishes_ttl_from_outage(env, reason):
    e = env
    opts = {}
    if reason == 'missing':
        e.sql.connection.execute('DELETE FROM run_objects')
        e.sql.connection.commit()
    elif reason == 'expired':
        e.now[0] += timedelta(days=14)
    elif reason == 'outage':
        e.sql.fail = lambda sql, params: 'FROM runs' in sql
    elif reason == 'acl':
        opts['actor'] = 'outsider'
    else:
        opts['settings'] = replace(e.settings, daily_budget_rub=Decimal(0))
    result = e.run(**opts)
    assert result.status == 'RED' and not e.adapters and not e.state['calls']
    assert e.sql.counter() is None and not e.state['pulls']
    if reason in ('missing', 'expired'):
        assert '14 дней' in result.message and 'doc_verify' in result.message
    if reason == 'outage':
        assert 'StorageError' in result.message and '14 дней' not in result.message


@pytest.mark.parametrize('mode', ['doc_translate', 'doc_verify'])
def test_three_admissions_survive_new_run_and_ttl(env, mode):
    e = env
    for count, greeting in enumerate(('Hello, world.', 'Hello world!', 'Hello, world!'), 1):
        previous_sha = e.remote_sha('topic')
        request_greeting(e, greeting)
        result = e.run()
        assert_changed_greeting(result, previous_sha, greeting)
        assert result.status == 'GREEN', result.message
        assert e.store.latest_context('up/docs/1')['continuation_count'] == count
    old = e.sql.rows()
    e.now[0] += timedelta(days=15)
    # Fresh completed context in either mode cannot erase original PR admission.
    fresh = RunStore(e.store, mode=mode, source_pr='up/docs/1')
    fresh.save(result)
    assert e.store.latest_context('up/docs/1')['original_pr'] == 'up/docs/2'
    made = len(e.adapters)
    denied = e.run()
    assert denied.status == 'RED' and 'три продолжения' in denied.message
    assert len(e.adapters) == made and e.sql.counter()['status'] == '3'
    assert all(row in e.sql.rows() for row in old)


def test_concurrent_component_admission_never_creates_fourth_model(env):
    e = env
    e.sql.gate = threading.Barrier(4)
    admitted = []
    def fail_factory(adapter):
        admitted.append(adapter.run_id)
        raise RuntimeError('admitted factory failure')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: e.run(model_factory=fail_factory), range(4)))
    assert len(admitted) == len(set(admitted)) == 3
    assert sum('три продолжения' in result.message for result in results) == 1
    assert all(result.status == 'RED' for result in results)
    assert e.sql.counter()['status'] == '3'
    assert not e.state['calls'] and not e.state['pulls']


def test_paid_failed_http_and_late_request_replay(env, monkeypatch):
    e = env
    previous = requests.Session.send
    def fail(session, request, **kwargs):
        response = previous(session, request, **kwargs)
        if 'model.invalid' in request.url:
            response.status_code = 400
            response._content = b'{"error":"paid rejected request","usage":{"cost":7.25}}'
        return response
    monkeypatch.setattr(requests.Session, 'send', fail)
    before = e.store.daily_cost()
    result = e.run()
    assert result.status == 'RED' and result.publication.draft
    assert result.attempts and all(a.error and a.status_code == 400 for a in result.attempts)
    cost = Decimal('7.25') * len(result.attempts)
    assert result.cost_breakdown['total'] == cost
    assert e.store.daily_cost() == before + cost
    adapter = RunStore(e.store, mode='doc_continue', source_pr='up/docs/2', run_id=e.adapters[-1].run_id)
    rows = e.sql.rows()
    for attempt in result.attempts:
        adapter.record_request(attempt.request)
    assert e.sql.rows() == rows and e.store.daily_cost() == before + cost
    assert e.sql.counter()['status'] == '1'


def test_same_pr_repair_then_continue_uses_final_sha(env):
    e = env
    e.first.source_pr = 'up/docs/1'
    e.first.save(e.seeded)
    request_greeting(e, 'Hello, world!')
    first = e.run()
    assert_changed_greeting(first, e.seeded.candidate_sha, 'Hello, world!')
    assert first.status == 'GREEN' and first.result_sha != e.seeded.result_sha
    context = e.store.latest_context('up/docs/1')
    assert context['source_sha'] == context['result_sha'] == first.result_sha
    request_greeting(e, 'Hello, world.')
    second = e.run()
    assert_changed_greeting(second, first.candidate_sha, 'Hello, world.')
    assert second.status == 'GREEN', second.message
    assert first.publication.pr_number == second.publication.pr_number == 1
    assert not e.state['pulls'] and e.remote_sha('topic')


def test_unselected_invalid_link_blocks_green_without_scope_expansion(env):
    e = env
    candidate = freeze(e.seeded.candidate, {ROOT+'en/other.md': b'# Other\n\n[Bad](missing.md)\n'})
    e.git('push', 'https://x-access-token:dummy@github.com/up/docs.git', candidate.sha+':refs/heads/topic')
    e.first.save(replace(e.seeded, candidate=candidate, checked_sha=candidate.sha,
                         publication=replace(e.seeded.publication, pushed_sha=candidate.sha)))
    result = e.run()
    assert result.status == 'RED' and result.publication.draft
    assert result.checked_sha == result.result_sha
    assert any(issue.path == ROOT+'en/other.md' for issue in result.issues)
    assert result.candidate.read(ROOT+'en/other.md') == candidate.read(ROOT+'en/other.md')
    assert all(data['path'] == ROOT+'en/a.md' for op, data in e.state['calls'])


@pytest.mark.parametrize('race', [False, True])
def test_fork_pr_identity_and_push_identity_are_separate(env, tmp_path, monkeypatch, race):
    e = env
    initial = e.remote_sha('topic')
    fork = tmp_path/'independent-fork.git'
    subprocess.run(['git', 'init', '--bare', str(fork)], check=True, capture_output=True)
    url = 'https://x-access-token:dummy@github.com/contributor/docs.git'
    e.git('config', f'url.{fork}.insteadOf', url)
    e.git('push', url, initial+':refs/heads/topic')
    e.state['source_repo'] = 'contributor/docs'
    def fork_sha():
        return subprocess.check_output(['git', '--git-dir', str(fork), 'rev-parse',
                                        'refs/heads/topic']).decode().strip()
    old_send = requests.Session.send
    def send(session, request, **kwargs):
        response = old_send(session, request, **kwargs)
        path = urlsplit(request.url).path
        if path == '/repos/up/docs/pulls/1':
            data = response.json()
            data['head']['sha'] = fork_sha()
            response._content = json.dumps(data).encode()
        elif path == '/repos/contributor/docs/git/ref/heads/topic':
            response._content = json.dumps(dict(object=dict(sha=fork_sha()))).encode()
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    foreign = []
    def handler(op, data):
        if op == 'repair' and race:
            concurrent = freeze(e.seeded.candidate, {'unrelated': b'foreign commit'})
            foreign.append(concurrent.sha)
            e.git('push', url, concurrent.sha+':refs/heads/topic')
        return GOOD if op == 'critic' else english_source(data).replace('Hello world.', 'Hello, world!')
    e.state['handler'] = handler
    result = e.run()
    assert e.remote_sha('topic') == initial  # upstream same-name branch untouched
    assert e.publisher.repository == 'up/docs' and not e.state['pulls']
    if race:
        assert result.status == 'RED' and result.result_sha is None
        assert fork_sha() == foreign[-1]  # lease protects the unrelated fork commit
    else:
        assert result.status == 'GREEN', result.message
        assert result.result_sha == result.checked_sha == fork_sha() != initial
        assert result.publication.repository == 'up/docs'
        assert result.publication.pr_number == 1
