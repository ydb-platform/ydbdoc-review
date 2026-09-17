"""Independent reacceptance: boundary snapshots and no repeated paid work.

Reuse offline HTTP/Git setup only; run the actual runners and YFM builder.
"""
from decimal import Decimal

import pytest

from ydbdoc_review.model import ModelClient
from ydbdoc_review.runner import RunHooks

from . import test_t10_independent as translate_fixtures
from . import test_verify_t11 as verify_fixtures

pytestmark = pytest.mark.timeout(120)
rig = translate_fixtures.rig
system = verify_fixtures.system
ROOT = translate_fixtures.ROOT


def retained(result, state, remote_sha, candidates):
    assert result.status == 'RED'
    assert result.result_sha == result.checked_sha == result.candidate_sha == remote_sha('translation')
    assert result.candidate.read(ROOT + 'en/a.md') == b'# Hello\n\nHello world.\n'
    assert candidates == [result.candidate]
    assert [op for op, _ in state['calls']] == ['translation', 'critic']
    assert len(result.attempts) == len(state['records']) == 2
    assert result.attempts == tuple(state['attempts'])
    assert dict(result.cost_breakdown) == dict(
        translation=Decimal('.25'), critic=Decimal('.25'), repair=Decimal(0), total=Decimal('.5'))
    assert len(state['pulls']) == state['made'] == state['admitted'] == 1


@pytest.mark.parametrize('boundary', ['close', 'save', 'report'])
def test_callbacks_observe_current_status_without_replay(rig, monkeypatch, boundary):
    state, run, _, _, _, remote_sha, _, _ = rig
    candidates, seen = [], []

    def callback(name, result):
        seen.append((name, result))
        assert result.result_sha == remote_sha('translation')
        if name == boundary:
            raise KeyboardInterrupt('callback interrupted')

    if boundary == 'close':
        original = ModelClient.close

        def close(client):
            original(client)
            raise RuntimeError('close failed')

        monkeypatch.setattr(ModelClient, 'close', close)
    result = run(hooks=RunHooks(candidate_progress=candidates.append,
                               save=lambda r: callback('save', r),
                               report=lambda r: callback('report', r)))
    retained(result, state, remote_sha, candidates)
    assert result.publication.draft and state['pulls'][0][1]['draft']
    assert result.cancelled == (boundary != 'close')
    assert [name for name, _ in seen] == ['save', 'report']
    expected = {'close': ['RED', 'RED'], 'save': ['GREEN', 'RED'], 'report': ['GREEN', 'GREEN']}
    assert [r.status for _, r in seen] == expected[boundary]
    for _, snapshot in seen:
        assert snapshot.publication.draft == (snapshot.status == 'RED')
        assert snapshot.attempts == result.attempts
        assert snapshot.candidate is result.candidate
    if boundary != 'report':
        assert seen[-1][1] == result
    else:
        assert not seen[-1][1].errors  # immutable pre-failure observation
        assert any('report:' in e for e in result.errors)


@pytest.mark.parametrize('draft_failure', ['raises', 'noop', 'readback'])
def test_failed_draft_is_visible_to_next_callback_without_retry(rig, monkeypatch, draft_failure):
    state, run, _, _, _, remote_sha, publisher, _ = rig
    candidates, seen, conversions = [], [], []
    original = publisher.github.convert_pull_to_draft

    def unavailable(*args):
        raise RuntimeError('readback unavailable')

    def convert(*args):
        conversions.append(args)
        if draft_failure == 'raises':
            raise RuntimeError('mutation unavailable')
        if draft_failure == 'readback':
            original(*args)
            monkeypatch.setattr(publisher.github, 'get_pull', unavailable)

    monkeypatch.setattr(publisher.github, 'convert_pull_to_draft', convert)

    def save(result):
        seen.append(('save', result))
        raise KeyboardInterrupt('save interrupted')

    def report(result):
        seen.append(('report', result))
        assert result.status == 'RED' and result.cancelled
        assert any('storage:' in e for e in result.errors)
        assert any('draft:' in e for e in result.errors)
        assert not result.publication.draft
        raise RuntimeError('report failed too')

    result = run(hooks=RunHooks(candidate_progress=candidates.append, save=save, report=report))
    retained(result, state, remote_sha, candidates)
    assert [name for name, _ in seen] == ['save', 'report']
    assert seen[0][1].status == 'GREEN' and not seen[0][1].errors
    assert len(conversions) == 1
    assert len(result.errors) == 3
    assert any('report:' in e for e in result.errors)
    assert not result.publication.draft
    assert state['pulls'][0][1]['draft'] == (draft_failure == 'readback')


@pytest.mark.parametrize('boundary', ['save', 'report'])
def test_verify_shared_hooks_keep_existing_pr_and_single_critic(system, boundary):
    state, run, _, _, _, remote_sha, _, _ = system
    seen = []

    def callback(name, result):
        seen.append((name, result))
        assert result.mode == 'doc_verify'
        if name == boundary:
            raise KeyboardInterrupt('verify callback interrupted')

    result = run(hooks=RunHooks(save=lambda r: callback('save', r),
                               report=lambda r: callback('report', r)))
    assert result.status == 'RED' and result.cancelled
    assert result.publication.pr_number == 1 and result.publication.draft and state['draft']
    assert result.result_sha == result.checked_sha == result.candidate_sha == remote_sha('topic')
    assert result.candidate.read(ROOT + 'en/a.md') == b'# Hello\n\nHello world.\n'
    assert [op for op, _ in state['calls']] == ['critic']
    assert len(result.attempts) == 1 and result.cost_breakdown['total'] == Decimal('.25')
    assert not state['pulls']
    assert [name for name, _ in seen] == ['save', 'report']
    assert [r.status for _, r in seen] == (['GREEN', 'RED'] if boundary == 'save'
                                         else ['GREEN', 'GREEN'])
