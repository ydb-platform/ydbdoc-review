"""Existing real runner/HTTP/Git/YFM fixture with production store hooks."""
# ruff: noqa: F811 -- Imported pytest fixtures.
from decimal import Decimal

import pytest

from tests.context_records import context_with_records
from tests.contract.test_translate_t10 import ROOT, system  # noqa: F401
from tests.unit.test_store_t12 import adapter, db, paid  # noqa: F401

pytestmark = pytest.mark.timeout(120)


@pytest.mark.parametrize('case', ['green', 'cancel', 'no_work', 'mechanical', 'budget'])
def test_runner_ydb_context_boundary(system, db, case):
    state, run, commit, _, _, _, _, _ = system
    store, boundary, _ = db
    persisted = adapter(store)
    if case == 'no_work':
        state['changes'] = []
    elif case == 'mechanical':
        commit({'en/a.md': '# Old translation\n', 'ru/a.md': None})
        state['changes'] = [dict(filename=ROOT+'ru/a.md', status='removed')]
    if case in ('no_work', 'mechanical', 'budget'):
        adapter(store).record_attempt(paid('100'))
    if case == 'cancel':
        def handler(operation, data):
            if operation == 'translation':
                state['cancelled'] = True
                return data.split('\n\n', 1)[1]
            raise AssertionError('critic after cancellation')
        state['handler'] = handler
    def factory():
        client = persisted.model_factory(cost_resolver=lambda e, r: Decimal('0.25'))
        def record(request):
            state['records'].append(request)  # input observation for HTTP fixture
            persisted.record_request(request)
        client.record_request = record
        return client
    result = run(model_factory=factory, admit=lambda: persisted.admit(Decimal(100)),
                 hooks=persisted.hooks(cancelled=lambda: state['cancelled']))
    context = context_with_records(store, persisted.run_id)
    assert context['source_sha'] == state['sha']
    assert context['result']['errors'] == list(result.errors)
    assert context['result']['status'] == result.status
    queries = [q for q, _ in boundary.calls if 'FROM runs' in q]
    assert len(queries) == (0 if case in ('no_work', 'mechanical') else 1)
    if case == 'green':
        assert result.status == 'GREEN', result.errors
        assert context['result_sha'] == result.result_sha == result.checked_sha
        assert context['final_files'][ROOT+'en/a.md'] == result.candidate.read(ROOT+'en/a.md')
        assert context['known_files'][0]['source']
        assert context['known_files'][0]['initial']['text']
        assert context['cost_breakdown']['total'] == Decimal('.50')
        assert len(context['attempts']) == 2
    elif case == 'cancel':
        assert result.cancelled
        assert context['cost_breakdown']['total'] == Decimal('.25')
        assert context['attempts'][0]['response_text']
        assert context['known_files'][0]['initial']
    elif case == 'budget':
        assert result.status == 'RED'
        assert not state['calls']
        assert 'YDBDOC_DAILY_BUDGET_RUB' in result.message
    else:
        assert not state['calls']
        assert context['cost_breakdown']['total'] == 0
        if case == 'mechanical':
            assert result.publication is not None, result.errors
            assert context['final_files'][ROOT+'en/a.md'] is None


@pytest.mark.parametrize('confirmed', [True, False])
def test_final_bytes_assets_issues_and_source_result_sha(git_repo, db, confirmed):
    from ydbdoc_review.document import FileResult
    from ydbdoc_review.links import Candidate
    from ydbdoc_review.plan import Snapshot
    from ydbdoc_review.publication import Publication, freeze
    from ydbdoc_review.quality import Issue
    from ydbdoc_review.quality_loop import SelectedFile
    from ydbdoc_review.runner import RunResult

    repo, git = git_repo
    source_sha = git('rev-parse', 'HEAD').decode().strip()
    original = Candidate.open(repo, source_sha)
    candidate = freeze(original, {'en/a.md': b'Final repaired\r\n', 'en/asset.png': b'\x00\xffPNG'})
    result = RunResult(
        snapshot=Snapshot('up', 'docs', 1, source_sha, source_sha, 'topic', 'up/docs'),
        candidate=candidate, checked_sha=candidate.sha,
        publication=Publication('up/docs', 'translation', 'topic', candidate.sha,
                                pr_number=2, head_confirmed=confirmed),
        files=(FileResult('en/a.md', 'Initial wrong text', (), False),),
        selected_files=(SelectedFile('en/a.md', 'Original source', 'en', 'Fix it',
                                     (('term', 'translation'),)),),
        issues=(Issue('en/a.md', 'Missing paragraph', 'Insert paragraph'),),
        unfinished_files=('en/a.md',), cancelled=True)
    store, _, _ = db
    run = adapter(store)
    run.save(result)
    data = context_with_records(store, run.run_id)
    assert data['source_sha'] == source_sha
    assert data['result_sha'] == (candidate.sha if confirmed else None)
    assert data['candidate_sha'] == data['result']['checked_sha'] == candidate.sha
    assert data['final_files'] == {'en/a.md': b'Final repaired\r\n', 'en/asset.png': b'\x00\xffPNG'}
    assert data['known_files'][0]['initial']['text'] == 'Initial wrong text'
    assert data['known_files'][0]['instruction'] == 'Fix it'
    assert data['result']['issues'][0]['problem'] == 'Missing paragraph'
    assert data['result']['unfinished_files'] == ['en/a.md']
