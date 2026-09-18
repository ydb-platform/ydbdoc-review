"""T12 gaps: actual repaired/published bytes, overrun, cancellation and rename.

Only HTTP send and YDB SDK boundaries are fixtures; all components are real.
Git writes stay inside the imported temporary local/bare repositories.
"""
# ruff: noqa: F811 -- Imported pytest fixtures.
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.context_records import context_with_records
from tests.contract.test_translate_t10 import GOOD, ROOT, system  # noqa: F401
from tests.unit.test_store_t12 import adapter, db  # noqa: F401

pytestmark = pytest.mark.timeout(120)


@pytest.mark.parametrize('cancel_after_repair', [False, True])
def test_repaired_published_context_overrun_and_next_gate(system, db, cancel_after_repair):
    state, run, commit, _, git, remote_sha, publisher, _ = system
    store, boundary, now = db
    now[0] = datetime.now(UTC)
    asset = b'<svg xmlns="http://www.w3.org/2000/svg"><path id="exact"/></svg>'
    source = '# Article\n\n[Link](/ru/b.md)\n\n![Picture](pic.svg)\n'
    commit({'ru/a.md': source, 'ru/b.md': '# B\n', 'en/b.md': '# B\n',
            'ru/pic.svg': asset})
    persisted = adapter(store)
    clients = []
    reports = []
    candidates = []

    def factory():
        client = persisted.model_factory(cost_resolver=lambda ep, response: Decimal('.25'))
        original_callback = client.record_request

        def record(request):
            original_callback(request)
            state['records'].append(request)

        client.record_request = record
        clients.append(client)
        return client

    def handler(operation, data):
        request = state['records'][-1]
        key = persisted._id(request)
        # Actual callbacks have persisted request and pending ledger before HTTP.
        assert store.get(persisted.run_id, 'request/' + key)
        assert boundary.runs[persisted.run_id, key]['status'] == 'pending'
        if operation == 'translation':
            return data.split('\n\n', 1)[1]
        if operation == 'critic':
            return GOOD
        assert operation == 'repair'
        return data['source']

    state['handler'] = handler

    def candidate_progress(candidate):
        persisted.candidate_progress(candidate)
        candidates.append(candidate.sha)
        if cancel_after_repair and len(candidates) == 2:
            state['cancelled'] = True

    hooks = replace(persisted.hooks(report=reports.append,
                                    cancelled=lambda: state['cancelled']),
                    candidate_progress=candidate_progress)
    result = run(model_factory=factory, admit=lambda: persisted.admit(Decimal('.10')),
                 hooks=hooks)
    expected_ops = ['translation', 'critic', 'repair']
    if not cancel_after_repair:
        expected_ops.append('critic')
    assert [op for op, _ in state['calls']] == expected_ops
    assert len(clients) == 1
    assert result.status == ('RED' if cancel_after_repair else 'GREEN'), result.errors
    assert result.cancelled == cancel_after_repair
    assert result.publication.draft == cancel_after_repair
    assert result.result_sha == remote_sha('translation') == candidates[-1]
    assert len(candidates) == 2 and candidates[0] != candidates[1]
    if cancel_after_repair:
        assert result.checked_sha != result.candidate_sha
    else:
        assert result.checked_sha == result.result_sha
        assert result.quality.rounds[-1].build.ok_for(result.checked_sha)

    context = context_with_records(store, persisted.run_id)
    expected_bytes = source.replace('/ru/b.md', '/en/b.md').encode()
    assert result.files[0].text.encode() != expected_bytes
    assert context['final_files'][ROOT + 'en/a.md'] == expected_bytes
    assert context['final_files'][ROOT + 'en/pic.svg'] == asset
    for path, content in context['final_files'].items():
        assert git('show', result.result_sha + ':' + path) == content
    assert context['source_sha'] == state['sha']
    assert context['result_sha'] == result.result_sha
    assert context['candidate_sha'] == result.candidate_sha
    assert context['result']['checked_sha'] == result.checked_sha
    assert context['result']['cancelled'] == cancel_after_repair
    assert context['result']['errors'] == list(result.errors)
    assert context['result']['unfinished_files'] == list(result.unfinished_files)
    expected_cost = dict(translation=Decimal('.25'), repair=Decimal('.25'),
                         critic=Decimal('.25') if cancel_after_repair else Decimal('.50'),
                         total=Decimal('.75') if cancel_after_repair else Decimal('1.00'))
    assert context['cost_breakdown'] == result.cost_breakdown == expected_cost
    assert reports == [result]
    assert len(context['requests']) == len(context['attempts']) == len(expected_ops)
    for saved, actual in zip(context['attempts'], result.attempts, strict=True):
        assert saved['response_text'] == actual.response_text
        assert saved['request']['payload'] == actual.request.payload
        assert saved['usage']['raw'] == actual.usage.raw
        assert saved['usage']['cost_rub'] == Decimal('.25')
    assert len([q for q, _ in boundary.calls if 'FROM runs' in q]) == 1

    # Same real runner, fresh admission; existing paid run now exceeds the limit.
    state['cancelled'] = False
    calls_before = len(state['calls'])
    next_run = adapter(store)
    publisher.branch = 'next-translation'

    def forbidden_factory():
        pytest.fail('Budget-blocked runner must not create a client')

    blocked = run(model_factory=forbidden_factory,
                  admit=lambda: next_run.admit(Decimal('.10')), hooks=next_run.hooks())
    assert blocked.status == 'RED' and 'YDBDOC_DAILY_BUDGET_RUB' in blocked.message
    assert blocked.publication is None
    assert len(state['calls']) == calls_before
    assert len([q for q, _ in boundary.calls if 'FROM runs' in q]) == 2
    assert store.context(next_run.run_id)['cost_breakdown']['total'] == 0
    # Replay after publication remains idempotent on the actual integration result.
    for request in clients[0].attempts:
        persisted.record_request(request.request)
    persisted.save(result)
    assert store.daily_cost() == expected_cost['total']
    assert context_with_records(store, persisted.run_id) == context


def test_mechanical_rename_green_with_unreadable_budget_and_exact_context(system, db):
    state, run, commit, _, git, remote_sha, _, _ = system
    store, boundary, _ = db
    text = '# Hello `code`\n'
    commit({'ru/a.md': None, 'ru/b.md': text, 'en/a.md': text})
    state['changes'] = [dict(filename=ROOT + 'ru/b.md', status='renamed',
                             previous_filename=ROOT + 'ru/a.md',
                             changes=0, additions=0, deletions=0)]
    boundary.fail = lambda query, params: 'FROM runs' in query
    persisted = adapter(store)

    def forbidden_factory():
        pytest.fail('Mechanical runner must not create a model')

    result = run(model_factory=forbidden_factory,
                 admit=lambda: persisted.admit(Decimal(0)), hooks=persisted.hooks())
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert not state['calls']
    assert not [q for q, _ in boundary.calls if 'FROM runs' in q]
    assert result.checked_sha == result.result_sha == remote_sha('translation')
    assert not result.publication.draft
    context = context_with_records(store, persisted.run_id)
    assert context['final_files'] == {ROOT + 'en/a.md': None, ROOT + 'en/b.md': text.encode()}
    assert git('show', result.result_sha + ':' + ROOT + 'en/b.md') == text.encode()
    assert result.candidate.read(ROOT + 'en/a.md') is None
    assert context['result_sha'] == context['result']['checked_sha'] == result.result_sha
    assert context['cost_breakdown'] == dict(translation=0, critic=0, repair=0, total=0)
    assert context['requests'] == context['attempts'] == []
