"""Real store/loop/model/Git/builder; stateful doubles only at SDK and HTTP."""
# ruff: noqa: F811, RUF001 -- pytest fixtures and Russian source documents.
import importlib
import json
import re
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
import ydb

from tests.contract.test_translate_t10 import system as translate_system  # noqa: F401
from tests.contract.test_verify_t11 import GOOD, ROOT, english_source, system  # noqa: F401
from tests.unit.test_store_t12 import Boundary
from ydbdoc_review.continuation import run_continue as run_continue
from ydbdoc_review.document import RequestBudget
from ydbdoc_review.model import Endpoint, ModelChoice
from ydbdoc_review.quality import Issue
from ydbdoc_review.runner import RunHooks
from ydbdoc_review.store import RunStore, YDBStore

pytestmark = pytest.mark.timeout(120)


class ContinueBoundary(Boundary):
    def execute(self, query, params, commit_tx):
        p = {k.removeprefix('$'): v for k, v in params.items()}
        if '$next AS continuation_count' in query:
            assert commit_tx
            self.calls.append((query, deepcopy(p)))
            if self.fail(query, p):
                raise OSError('YDB offline injected')
            key = (p['key'], 'summary')
            prior = self.runs.get(key, {})
            count = int(prior.get('status', 0)) + (prior.get('payload') != p['claim'])
            if count <= 3:
                self.runs[key] = dict(run_id=p['key'], entry_id='summary', status=str(count),
                                     payload=p['claim'], created_at=p['created'])
            return [SimpleNamespace(rows=[SimpleNamespace(continuation_count=count)])]
        if "entry_id='summary' AND run_id > $after" in query:
            self.calls.append((query, deepcopy(p)))
            if self.fail(query, p):
                raise OSError('YDB offline injected')
            rows = [SimpleNamespace(**v) for v in self.runs.values()
                    if v['entry_id'] == 'summary' and v['run_id'] > p['after']]
            return [SimpleNamespace(rows=sorted(rows, key=lambda r: r.run_id)[:1000])]
        # Metadata summaries have no billing/day columns, as on the real schema.
        if 'WHERE day=$day' in query:
            self.calls.append((query, deepcopy(p)))
            if self.fail(query, p):
                raise OSError('YDB offline injected')
            rows = [SimpleNamespace(**v) for v in self.runs.values()
                    if v.get('day') == p['day'] and v['entry_id'] != 'summary'
                    and (v['run_id'], v['entry_id']) > (p['after_run'], p['after_entry'])]
            return [SimpleNamespace(rows=sorted(rows, key=lambda r: (r.run_id, r.entry_id))[:1000])]
        return super().execute(query, params, commit_tx)


@pytest.fixture
def continued(system, monkeypatch):
    from datetime import UTC, datetime
    state, verify, commit, repo, git, remote_sha, publisher, settings = system
    commit({'ru/good.md': '# Хорошо\n\nХорошо.\n',
            'en/good.md': b'# Good\r\n\r\nUNCHANGED_SENTINEL.\r\n'})
    state['changes'].append(dict(filename=ROOT+'ru/good.md', status='added'))
    boundary = ContinueBoundary()
    monkeypatch.setattr(ydb, 'SessionPool', lambda driver: boundary)
    now = [datetime(2026, 9, 18, 12, tzinfo=UTC)]
    store = YDBStore(boundary, clock=lambda: now[0])
    source_pr = 'up/docs/2'
    state['source_head'] = state['sha']
    state['comments'] = [dict(body='/ydbdoc continue Исправь ydb/docs/en/a.md',
                              created_at='2026-09-18T11:00:00Z',
                              user=dict(login='writer', type='User'))]
    old_send = requests.Session.send
    def send(session, req, **kw):
        path = urlsplit(req.url).path
        if path.endswith('/pulls/2'):
            r = requests.Response()
            r.status_code = 200
            r._content = json.dumps(dict(state='open', merged=False,
                head=dict(sha=state['source_head'], ref='source', repo=dict(full_name='up/docs')),
                base=dict(ref='main'))).encode()
            return r
        if '/comments' in path:
            r = requests.Response()
            r.status_code = 200
            r._content = json.dumps(state['comments']).encode()
            return r
        return old_send(session, req, **kw)
    monkeypatch.setattr(requests.Session, 'send', send)
    adapters = []
    def factory(adapter):
        adapters.append(adapter)
        state['made'] += 1
        client = adapter.model_factory(cost_resolver=lambda ep, data: Decimal('.25'))
        def record(request):
            state['records'].append(request)
            adapter.record_request(request)
        client.record_request = record
        return client
    initial = RunStore(store, mode='doc_verify', source_pr=source_pr)
    seeded = verify(model_factory=lambda: factory(initial), hooks=initial.hooks())
    assert seeded.status == 'GREEN', seeded.errors
    original = store.context(initial.run_id)
    state['calls'].clear()
    state['made'] = 0
    adapters.clear()
    state['handler'] = lambda op, data: GOOD if op == 'critic' else english_source(data)
    choice = ModelChoice(Endpoint('eliza', 'https://model.invalid', 'main', 'dummy'))
    def run(**kw):
        now[0] += timedelta(seconds=1)
        opts = dict(repo=repo, github=publisher.github, owner='up', repository='docs', pr_number=1,
                    actor='writer', settings=settings, publisher=publisher, store=store,
                    critic_choice=choice, repair_choice=choice,
                    budget=RequestBudget(100000, 20000, lambda m: len(str(m))),
                    model_factory=factory, hooks=RunHooks(report=state['reported'].append))
        opts.update(kw)
        return run_continue(**opts)
    return SimpleNamespace(state=state, store=store, boundary=boundary, now=now, run=run,
                           initial=initial, original=original, seeded=seeded, repo=repo, git=git,
                           remote_sha=remote_sha, publisher=publisher, settings=settings,
                           adapters=adapters, verify=verify, factory=factory, commit=commit)


def request_greeting(c, greeting):
    c.state['comments'][0]['body'] = f'/ydbdoc continue In ydb/docs/en/a.md use greeting "{greeting}"'
    c.state['handler'] = lambda op, data: GOOD if op == 'critic' else re.sub(
        r'Hello,? world[.!]', greeting, english_source(data))


def assert_changed_greeting(result, previous_sha, greeting):
    assert result.candidate_sha != previous_sha
    assert result.candidate.text(ROOT+'en/a.md') == f'# Hello\n\n{greeting}\n'
    assert [r.candidate_sha for r in result.quality.rounds] == [previous_sha, result.checked_sha]


def test_real_continue_three_then_four_new_runs_costs_and_known_good(continued):
    c = continued
    before = deepcopy(c.boundary.runs)
    for n, greeting in enumerate(('Hello, world.', 'Hello world!', 'Hello, world!'), 1):
        previous_sha = c.remote_sha('topic')
        request_greeting(c, greeting)
        result = c.run()
        assert_changed_greeting(result, previous_sha, greeting)
        assert result.status == 'GREEN', (result.errors, result.issues)
        assert result.mode == 'doc_continue'
        assert result.result_sha == result.checked_sha == c.remote_sha('topic')
        assert result.publication.pr_number == 1 and result.publication.repository == 'up/docs'
        assert result.files == () and result.cost_breakdown['translation'] == 0
        assert result.cost_breakdown['total'] == Decimal('.75')
        assert result.candidate.read(ROOT+'en/good.md') == b'# Good\r\n\r\nUNCHANGED_SENTINEL.\r\n'
        assert [f.path for f in result.selected_files] == [ROOT+'en/a.md']
        assert result.quality.rounds[-1].build.ok_for(result.checked_sha)
        assert result.quality.rounds[-1].links.candidate_sha == result.checked_sha
        latest = c.store.latest_context('up/docs/2')
        assert latest == c.store.latest_context('up/docs/1')
        assert latest['run_id'] == c.adapters[-1].run_id != c.initial.run_id
        assert latest['continuation_count'] == n
        assert latest['source_sha'] == c.state['source_head']
        assert latest['result_sha'] == result.result_sha
        assert len(latest['known_files']) == 2
        assert len(latest['attempts']) == 3
    refused = c.run()
    assert refused.status == 'RED' and 'три продолжения' in refused.message
    assert c.state['made'] == 3
    assert c.store.context(c.initial.run_id) == c.original
    for key, value in before.items():
        assert c.boundary.runs[key] == value  # prior ledger/summary never rewritten
    assert not c.state['pulls'] and c.remote_sha('topic')
    assert all(op in {'critic', 'repair'} for op, _ in c.state['calls'])
    assert 'UNCHANGED_SENTINEL' not in json.dumps(c.state['calls'], ensure_ascii=False)
    assert c.state['reported'][-1] == refused


@pytest.mark.parametrize('side', ['source', 'result'])
def test_unrelated_actual_commit_on_either_pr_refuses_before_client(continued, side):
    c = continued
    c.git('commit', '--allow-empty', '-m', 'unrelated commit')
    sha = c.git('rev-parse', 'HEAD').decode().strip()
    if side == 'source':
        c.state['source_head'] = sha
    else:
        c.git('push', 'https://x-access-token:dummy@github.com/up/docs.git', sha+':refs/heads/topic')
    result = c.run()
    assert result.status == 'RED'
    assert importlib.import_module('ydbdoc_review.continuation').CHANGED in result.message
    assert c.state['made'] == 0 and not c.state['calls']
    assert ('continuations/up/docs/2', 'summary') not in c.boundary.runs


@pytest.mark.parametrize('case', ['missing', 'expired', 'outage', 'acl', 'ambiguous', 'budget', 'publisher'])
def test_preflight_refusals_not_counted_and_storage_distinct(continued, case):
    c = continued
    opts = {}
    if case == 'missing':
        c.boundary.objects.clear()
    elif case == 'expired':
        c.now[0] += timedelta(days=14)
    elif case == 'outage':
        c.boundary.fail = lambda q, p: 'FROM runs' in q
    elif case == 'acl':
        opts['actor'] = 'outsider'
    elif case == 'ambiguous':
        c.state['comments'][0]['body'] = '/ydbdoc continue Сделай лучше'
    elif case == 'budget':
        opts['settings'] = replace(c.settings, daily_budget_rub=Decimal(0))
    elif case == 'publisher':
        opts['publisher'] = replace(c.publisher, pr_number=None)
    result = c.run(**opts)
    assert result.status == 'RED' and c.state['made'] == 0
    assert not c.state['calls'] and not c.state['pulls']
    assert ('continuations/up/docs/2', 'summary') not in c.boundary.runs
    if case in {'missing', 'expired'}:
        assert '14 дней' in result.message and 'doc_verify' in result.message
    if case == 'outage':
        assert 'StorageError' in result.message and '14 дней' not in result.message


def test_explicit_successful_file_latest_allowed_instruction(continued):
    c = continued
    c.state['comments'] += [
        dict(body='/ydbdoc continue Исправь ydb/docs/en/good.md', created_at='2026-09-18T11:01:00Z',
             user=dict(login='writer', type='User')),
        dict(body='/ydbdoc continue ydb/docs/en/a.md', created_at='2026-09-18T11:02:00Z',
             user=dict(login='outsider', type='User'))]
    c.state['handler'] = lambda op, data: GOOD if op == 'critic' else data['source'].replace('Хорошо', 'Good')
    result = c.run()
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert [f.path for f in result.selected_files] == [ROOT+'en/good.md']
    assert result.candidate.read(ROOT+'en/a.md') == c.seeded.candidate.read(ROOT+'en/a.md')
    assert [op for op, _ in c.state['calls']] == ['critic', 'repair', 'critic']
    repair = c.state['calls'][1][1]
    assert 'Инструкция техписа' in repair['findings'][0]['problem']
    assert 'attempts' not in repair and 'requests' not in repair


def test_saved_problem_forces_common_loop_repair_even_when_critic_correct(continued):
    c = continued
    c.initial.save(replace(c.seeded, status='RED', issues=(
        Issue(ROOT+'en/a.md', 'Saved specific problem: greeting punctuation', 'Use greeting Hello, world.'),)))
    request_greeting(c, 'Hello, world.')
    c.state['comments'][0]['body'] = '/ydbdoc continue Исправь сохранённые замечания'
    result = c.run()
    assert result.status == 'GREEN', result.errors
    assert [op for op, _ in c.state['calls']] == ['critic', 'repair', 'critic']
    assert_changed_greeting(result, c.seeded.candidate_sha, 'Hello, world.')
    assert any(f['problem'] == 'Saved specific problem: greeting punctuation' for f in c.state['calls'][1][1]['findings'])


def test_new_verify_does_not_reset_source_pr_limit(continued):
    c = continued
    for greeting in ('Hello, world.', 'Hello world!', 'Hello, world!'):
        previous_sha = c.remote_sha('topic')
        request_greeting(c, greeting)
        result = c.run()
        assert result.status == 'GREEN'
        assert_changed_greeting(result, previous_sha, greeting)
    c.now[0] += timedelta(seconds=1)
    fresh = RunStore(c.store, mode='doc_verify', source_pr='up/docs/1')
    renewed = c.verify(model_factory=lambda: c.factory(fresh), hooks=fresh.hooks())
    assert renewed.status == 'GREEN', renewed.errors
    before = c.state['made']
    refused = c.run()
    assert 'три продолжения' in refused.message and c.state['made'] == before
    assert c.store.context(fresh.run_id)['continuation_count'] == 0


@pytest.mark.parametrize('mode', ['doc_translate', 'doc_verify'])
def test_durable_limit_survives_context_expiry_and_new_run(continued, mode):
    c = continued
    for n in range(3):
        assert c.store.claim_continuation('up/docs/2', f'admitted-{n}') == n + 1
    c.now[0] += timedelta(days=15)
    fresh = RunStore(c.store, mode=mode, source_pr='up/docs/2')
    fresh.save(c.seeded)
    result = c.run()
    assert 'три продолжения' in result.message and c.state['made'] == 0
    assert c.store.daily_cost() == 0  # counter metadata is not a financial attempt


@pytest.mark.parametrize('failure', ['factory', 'critic_interrupt', 'save'])
def test_admitted_failure_consumes_slot_and_keeps_actual_evidence(continued, failure):
    c = continued
    opts = {}
    if failure == 'factory':
        def broken(adapter):
            raise RuntimeError('factory unavailable')
        opts['model_factory'] = broken
    elif failure == 'critic_interrupt':
        c.state['handler'] = lambda op, data: KeyboardInterrupt('stop now')
    else:
        def failed_save(result):
            raise OSError('reporting observer storage error')
        opts['hooks'] = RunHooks(save=failed_save, report=c.state['reported'].append)
    result = c.run(**opts)
    assert result.status == 'RED'
    assert c.boundary.runs['continuations/up/docs/2', 'summary']['status'] == '1'
    if failure != 'factory':
        assert result.publication and result.publication.draft
        assert c.remote_sha('topic') == result.result_sha
        assert result.attempts
    if failure == 'critic_interrupt':
        assert result.cancelled and result.unfinished_files
        assert result.cost_breakdown['total'] is None
        assert result.quality.rounds


def test_full_tree_unselected_broken_link_blocks_green_without_repairing_it(continued):
    from ydbdoc_review.publication import freeze
    c = continued
    broken = freeze(c.seeded.candidate, {ROOT+'en/unselected.md': b'# Other\n\n[Missing](missing.md)\n'})
    c.git('push', 'https://x-access-token:dummy@github.com/up/docs.git', broken.sha+':refs/heads/topic')
    c.initial.save(replace(c.seeded, candidate=broken, checked_sha=broken.sha,
                           publication=replace(c.seeded.publication, pushed_sha=broken.sha)))
    result = c.run()
    assert result.status == 'RED' and result.publication.draft
    assert len(result.quality.rounds) == 1  # §5.1: selected repair is a no-op; global error remains
    assert any(i.path == ROOT+'en/unselected.md' for i in result.issues)
    assert result.candidate.read(ROOT+'en/unselected.md') == broken.read(ROOT+'en/unselected.md')
    assert all(data['path'] != ROOT+'en/unselected.md' for _, data in c.state['calls'])
    assert result.checked_sha == result.result_sha


@pytest.mark.parametrize('race', [False, True])
def test_continue_writable_fork_uses_shared_publisher_and_exact_lease(continued, tmp_path, monkeypatch, race):
    import subprocess

    from ydbdoc_review.publication import freeze
    c = continued
    initial = c.remote_sha('topic')
    fork = tmp_path / 'continue-fork.git'
    subprocess.run(['git', 'init', '--bare', str(fork)], check=True, capture_output=True)
    url = 'https://x-access-token:dummy@github.com/contributor/docs.git'
    c.git('config', f'url.{fork}.insteadOf', url)
    c.git('push', url, initial+':refs/heads/topic')
    c.state['source_repo'] = 'contributor/docs'
    def fork_sha():
        return subprocess.check_output(['git', '--git-dir', str(fork), 'rev-parse',
                                        'refs/heads/topic']).decode().strip()
    transport = requests.Session.send
    def send(session, request, **kwargs):
        response = transport(session, request, **kwargs)
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
            tree = freeze(c.seeded.candidate, {'foreign': b'concurrent commit'})
            foreign.append(tree.sha)
            c.git('push', url, tree.sha+':refs/heads/topic')
        return GOOD if op == 'critic' else english_source(data).replace('Hello world.', 'Hello, world!')
    c.state['handler'] = handler
    result = c.run()
    assert c.remote_sha('topic') == initial
    assert c.publisher.repository == 'up/docs'
    assert not c.state['pulls']
    if race:
        assert result.status == 'RED' and result.result_sha is None
        assert fork_sha() == foreign[-1]
    else:
        assert result.status == 'GREEN', result.errors
        assert result.result_sha == result.checked_sha == fork_sha() != initial
        assert result.publication.repository == 'up/docs'


def test_counter_idempotency_and_lookup_pages(continued):
    from ydbdoc_review.store import encode
    c = continued
    for n in range(1002):
        c.store.ledger(f'old-{n:04}', 'summary', created=c.now[0]-timedelta(days=1),
                       mode='doc_verify', status='RED', payload=encode({'source_pr': 'unrelated/other/1'}))
    assert c.store.latest_context('up/docs/1')['run_id'] == c.initial.run_id
    assert c.store.claim_continuation('up/docs/2', 'one') == 1
    assert c.store.claim_continuation('up/docs/2', 'one') == 1
    assert c.store.claim_continuation('up/docs/2', 'two') == 2
    assert c.store.claim_continuation('up/docs/2', 'three') == 3
    with pytest.raises(ValueError, match='три'):
        c.store.claim_continuation('up/docs/2', 'four')


def test_translate_then_continue_actual_components(translate_system, monkeypatch):
    from datetime import UTC, datetime
    state, translate, _, repo, _, remote_sha, publisher, settings = translate_system
    boundary = ContinueBoundary()
    monkeypatch.setattr(ydb, 'SessionPool', lambda driver: boundary)
    now = [datetime(2026, 9, 18, 12, tzinfo=UTC)]
    class ModelClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0].astimezone(tz)
    monkeypatch.setattr('ydbdoc_review.model.datetime', ModelClock)
    store = YDBStore(boundary, clock=lambda: now[0])
    initial = RunStore(store, source_pr='up/docs/1', mode='doc_translate')
    adapters = []
    def factory(adapter):
        adapters.append(adapter)
        client = adapter.model_factory(cost_resolver=lambda ep, data: Decimal('.25'))
        def record(request):
            state['records'].append(request)
            adapter.record_request(request)
        client.record_request = record
        return client
    first = translate(model_factory=lambda: factory(initial), hooks=initial.hooks(),
                      admit=lambda: initial.admit(settings.daily_budget_rub))
    assert first.status == 'GREEN', first.errors
    assert len(state['pulls']) == 1
    initial_context = store.context(initial.run_id)
    transport = requests.Session.send
    def send(session, request, **kwargs):
        if '/comments' in urlsplit(request.url).path:
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps([dict(
                body='/ydbdoc continue In ydb/docs/en/a.md use greeting "Hello, world!"',
                created_at='2026-09-18T11:00:00Z', user=dict(login='writer', type='User'))]).encode()
            return response
        return transport(session, request, **kwargs)
    monkeypatch.setattr(requests.Session, 'send', send)
    state['calls'].clear()
    state['handler'] = lambda op, data: GOOD if op == 'critic' else data['source'].replace('Hello world.', 'Hello, world!')
    choice = ModelChoice(Endpoint('eliza', 'https://model.invalid', 'main', 'dummy'))
    now[0] += timedelta(seconds=1)
    result = run_continue(repo=repo, github=publisher.github, owner='up', repository='docs', pr_number=2,
                          actor='writer', settings=settings, publisher=replace(publisher, pr_number=2),
                          store=store, critic_choice=choice, repair_choice=choice,
                          budget=RequestBudget(100000, 20000, lambda m: len(str(m))),
                          model_factory=factory, hooks=RunHooks(report=state['reported'].append))
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert_changed_greeting(result, first.candidate_sha, 'Hello, world!')
    assert result.result_sha == remote_sha('translation') == result.checked_sha != first.result_sha
    assert result.publication.pr_number == 2 and len(state['pulls']) == 1
    assert result.snapshot.source_sha == state['sha']
    assert [op for op, _ in state['calls']] == ['critic', 'repair', 'critic']
    assert result.cost_breakdown['total'] == Decimal('.75')
    assert store.daily_cost() == Decimal('1.25')
    assert store.context(initial.run_id) == initial_context
    latest = store.latest_context('up/docs/1')
    assert latest['run_id'] == adapters[-1].run_id != initial.run_id
    assert latest['result_sha'] == result.result_sha
    assert state['reported'] == [result]


def test_same_pr_repairs_save_final_source_sha_for_next_continue(continued):
    c = continued
    c.initial.source_pr = 'up/docs/1'
    c.initial.save(c.seeded)
    request_greeting(c, 'Hello, world!')
    first = c.run()
    assert_changed_greeting(first, c.seeded.candidate_sha, 'Hello, world!')
    assert first.status == 'GREEN', first.errors
    assert first.result_sha != c.seeded.result_sha
    context = c.store.latest_context('up/docs/1')
    assert context['source_sha'] == context['result_sha'] == first.result_sha
    request_greeting(c, 'Hello, world.')
    second = c.run()
    assert_changed_greeting(second, first.candidate_sha, 'Hello, world.')
    assert second.status == 'GREEN', second.errors
    assert c.store.latest_context('up/docs/1')['continuation_count'] == 2


def test_actual_store_final_write_failure_is_red_draft_and_consumes_slot(continued):
    c = continued
    request_greeting(c, 'Hello, world.')
    failed_context_writes = []
    def fail_final_context(query, params):
        failed = 'UPSERT INTO run_objects' in query and params.get('object_key') == 'context'
        if failed:
            failed_context_writes.append(dict(params))
        return failed
    c.boundary.fail = fail_final_context
    result = c.run()
    assert failed_context_writes
    assert {p['run_id'] for p in failed_context_writes} == {c.adapters[-1].run_id}
    assert_changed_greeting(result, c.seeded.candidate_sha, 'Hello, world.')
    assert [op for op, _ in c.state['calls']] == ['critic', 'repair', 'critic']
    ledger = [row for (run_id, entry), row in c.boundary.runs.items()
              if run_id == c.adapters[-1].run_id and entry != 'summary']
    assert len(ledger) == 3
    assert sum(Decimal(row['cost_rub']) for row in ledger) == Decimal('.75')
    assert result.status == 'RED' and result.publication.draft
    assert 'storage: StorageError' in result.message
    assert result.checked_sha == result.result_sha == c.remote_sha('topic')
    assert result.cost_breakdown['total'] == Decimal('.75')
    assert c.boundary.runs['continuations/up/docs/2', 'summary']['status'] == '1'
    c.boundary.fail = lambda q, p: False
    assert c.store.context(c.initial.run_id) == c.original
