"""Fresh T13 acceptance on the current tree; only external boundaries mocked."""
# ruff: noqa: F811
import importlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
import requests

from tests.contract.test_t13_independent import (
    assert_changed_greeting,
    env,  # noqa: F401 -- imported pytest fixture
    request_greeting,
)
from tests.contract.test_verify_t11 import GOOD, ROOT, english_source, system  # noqa: F401
from ydbdoc_review.document import RequestBudget
from ydbdoc_review.plan import PlanError
from ydbdoc_review.quality import Issue
from ydbdoc_review.quality_loop import SelectedFile
from ydbdoc_review.runner import run_translate
from ydbdoc_review.store import RunStore

module = 'ydbdoc_review.continuation' if importlib.util.find_spec('ydbdoc_review.continuation') else 'ydbdoc_review.continue'
select_files = importlib.import_module(module).select_files
pytestmark = pytest.mark.timeout(120)


def saved(lang='en', names=('a.md', 'data.md', 'stable.md'), issues=(), unfinished=()):
    files = [asdict(SelectedFile(ROOT + lang + '/' + name, '# Original\n', lang)) for name in names]
    return dict(known_files=files, result=dict(selected_files=files,
                issues=[asdict(i) for i in issues], unfinished_files=list(unfinished)))


@pytest.mark.parametrize('lang', ['ru', 'en'])
@pytest.mark.parametrize('alias', ['data.md', 'ydb/docs/ru/data.md', 'ydb/docs/en/data.md'])
def test_unique_basename_and_language_path_aliases(lang, alias):
    result = select_files(saved(lang), 'Improve `' + alias + '`')
    assert [f.path for f in result] == [ROOT + lang + '/data.md']
    assert result[0].instruction == 'Improve `' + alias + '`'


def test_ambiguous_basename_enumerates_candidates_before_admission(env):
    e = env
    extra = replace(e.seeded.selected_files[0], path=ROOT+'en/nested/a.md')
    e.first.save(e.seeded, known_files=(*e.seeded.selected_files, extra))
    e.comments[0]['body'] = '/ydbdoc continue Fix a.md'
    before = e.remote_sha('topic'), e.source_sha[0], e.store.daily_cost()
    result = e.run()
    assert result.status == 'RED'
    assert 'Неоднозначный' in result.message
    assert ROOT+'en/a.md' in result.message and ROOT+'en/nested/a.md' in result.message
    assert not e.adapters and not e.state['calls'] and e.sql.counter() is None
    assert (e.remote_sha('topic'), e.source_sha[0], e.store.daily_cost()) == before
    assert not e.state['pulls']


@pytest.mark.parametrize('lang', ['en', 'ru'])
def test_instruction_assignment_has_no_data_a_substring_collision(lang):
    prefix = ROOT+lang+'/'
    context = saved(lang, issues=(Issue(prefix+'a.md', 'Saved defect', 'Keep meaning'),))
    text = 'Shared glossary\nFix data.md: DATA_ONLY\nFix '+ROOT+'ru/a.md: A_ONLY'
    result = {f.path: f for f in select_files(context, text)}
    assert set(result) == {prefix+'a.md', prefix+'data.md'}
    assert result[prefix+'a.md'].instruction == 'Shared glossary\nFix '+ROOT+'ru/a.md: A_ONLY'
    assert result[prefix+'data.md'].instruction == 'Shared glossary\nFix data.md: DATA_ONLY'
    assert 'DATA_ONLY' not in str(result[prefix+'a.md'].requested_findings)
    assert 'A_ONLY' not in str(result[prefix+'data.md'].requested_findings).replace('DATA_ONLY', '')
    assert result[prefix+'a.md'].requested_findings[0].problem == 'Saved defect'
    # data.md alone must not implicitly select the successful a.md sibling.
    assert [f.path for f in select_files(saved(lang), 'Fix data.md')] == [prefix+'data.md']


def test_only_errors_unfinished_and_explicit_files_are_selected():
    context = saved(names=('bad.md', 'unfinished.md', 'chosen.md', 'warn.md', 'good.md'),
                    issues=(Issue(ROOT+'en/bad.md', 'Bad', 'Repair'),
                            Issue(ROOT+'en/warn.md', 'Warning', 'Observe', severity='warning')),
                    unfinished=(ROOT+'en/unfinished.md',))
    result = select_files(context, 'Fix chosen.md and '+ROOT+'ru/chosen.md')
    assert [f.path for f in result] == [ROOT+'en/'+p for p in ('bad.md', 'chosen.md', 'unfinished.md')]
    assert result[0].instruction == result[2].instruction == ''
    assert len(result[1].requested_findings) == 1


@pytest.mark.parametrize('alias', ['unknown/data.md', 'missing.md'])
def test_unknown_path_never_falls_back_to_known_basename(alias):
    with pytest.raises(PlanError, match='Неизвестные'):
        select_files(saved(), 'Fix '+alias)


@pytest.mark.parametrize('mode', ['doc_verify', 'doc_translate'])
def test_real_new_mode_after_ttl_does_not_reset_original_pr_limit(env, monkeypatch, mode):
    e = env
    source_before = e.source_sha[0]
    # Refused preflight followed by actual admissions: refusals do not use slots.
    denied = e.run(actor='outsider')
    assert denied.status == 'RED' and e.sql.counter() is None and not e.adapters
    for count, greeting in enumerate(('Hello, world.', 'Hello world!', 'Hello, world!'), 1):
        previous_sha = e.remote_sha('topic')
        request_greeting(e, greeting)
        result = e.run()
        assert_changed_greeting(result, previous_sha, greeting)
        assert result.status == 'GREEN', result.message
        assert e.sql.counter()['status'] == str(count)
    prior_rows = e.sql.rows()
    old_run = e.adapters[-1].run_id
    e.now[0] += timedelta(days=15)
    expired = e.run()
    assert expired.status == 'RED' and '14 дней' in expired.message
    assert e.sql.counter()['status'] == '3'
    # Emulate actual YDB TTL deletion, keeping the durable runs ledger.
    e.sql.connection.execute('DELETE FROM run_objects')
    e.sql.connection.commit()
    class ModelClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return e.now[0].astimezone(tz)
    monkeypatch.setattr('ydbdoc_review.model.datetime', ModelClock)
    source_pr = 1 if mode == 'doc_verify' else 2
    fresh = RunStore(e.store, mode=mode, source_pr='up/docs/'+str(source_pr))
    e.state['handler'] = lambda op, data: GOOD if op == 'critic' else (
        english_source({'source': data.split('\n\n', 1)[1]}).replace('Hello world.', 'Hello, world!')
        if op == 'translation' else english_source(data))
    e.state['changes'] = [dict(filename=ROOT+'ru/a.md', status='modified')]
    e.state['calls'].clear()
    cost_before = e.store.daily_cost()
    publisher = e.publisher
    if mode == 'doc_verify':
        renewed = e.verify(model_factory=lambda: e.factory(fresh), hooks=fresh.hooks(),
                           admit=lambda: fresh.admit(Decimal(10000)))
        target_pr = 1
    else:
        # Real translate and real local bare push to a NEW translation PR/branch.
        publisher = replace(e.publisher, branch='renewed-translation', pr_number=None, base=None)
        previous_send = requests.Session.send
        def send(session, request, **kwargs):
            path = urlsplit(request.url).path
            payload = None
            if path == '/repos/up/docs/pulls' and request.method == 'GET':
                payload = []
            elif path == '/repos/up/docs/pulls' and request.method == 'POST':
                e.state['pulls'].append(json.loads(request.body))
                payload = dict(number=3, html_url='https://github.com/up/docs/pull/3')
            elif path == '/repos/up/docs/pulls/2/files':
                payload = e.state['changes']
            elif path == '/repos/up/docs/pulls/3':
                payload = dict(state='open', merged=False, draft=False,
                               head=dict(sha=e.remote_sha('renewed-translation'), ref='renewed-translation',
                                         repo=dict(full_name='up/docs')), base=dict(ref='source'))
            if payload is None:
                return previous_send(session, request, **kwargs)
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(payload).encode()
            return response
        monkeypatch.setattr(requests.Session, 'send', send)
        renewed = run_translate(repo=e.repo, github=publisher.github, owner='up', repository='docs',
                                pr_number=source_pr, actor='writer', settings=e.settings, publisher=publisher,
                                model_factory=lambda: e.factory(fresh), hooks=fresh.hooks(),
                                admit=lambda: fresh.admit(Decimal(10000)), translation_choice=e.choice,
                                critic_choice=e.choice, repair_choice=e.choice,
                                budget=RequestBudget(100000, 20000, lambda m: len(str(m))))
        target_pr = 3
    assert renewed.status == 'GREEN', renewed.message
    assert renewed.mode == mode and renewed.result_sha == renewed.checked_sha
    assert e.store.daily_cost() == cost_before + renewed.cost_breakdown['total']
    assert renewed.cost_breakdown['total'] > 0
    assert ('translation' in [op for op, _ in e.state['calls']]) == (mode == 'doc_translate')
    context = e.store.latest_context('up/docs/'+str(target_pr))
    assert context['run_id'] == fresh.run_id != old_run
    assert context['original_pr'] == 'up/docs/2'
    made = len(e.adapters)
    e.state['calls'].clear()
    branch_before = e.remote_sha(publisher.branch)
    denied = e.run(pr_number=target_pr, publisher=replace(publisher, pr_number=target_pr))
    assert denied.status == 'RED' and 'три продолжения' in denied.message
    assert len(e.adapters) == made and not e.state['calls']
    assert e.sql.counter()['status'] == '3'
    assert e.remote_sha(publisher.branch) == branch_before and e.source_sha[0] == source_before
    assert all(row in e.sql.rows() for row in prior_rows)
