"""Late failures must retain the published bytes and tell the truth about draft."""
from dataclasses import replace

import pytest

from ydbdoc_review.model import ModelClient
from ydbdoc_review.runner import RunCancelled, RunHooks

from . import test_t10_independent as fixtures

pytestmark = pytest.mark.timeout(120)
rig = fixtures.rig
ROOT = fixtures.ROOT


def assert_retained(result, state, remote_sha, candidates):
    assert result.status == 'RED'
    assert result.result_sha == result.checked_sha == result.candidate_sha == remote_sha('translation')
    assert candidates == [result.candidate]
    assert result.candidate.text(ROOT + 'en/a.md') == '# Hello\n\nHello world.\n'
    assert result.attempts == tuple(state['attempts'])
    assert result.errors
    assert len(state['pulls']) == 1


@pytest.mark.parametrize('boundary', ['close', 'save', 'report', 'cancel'])
@pytest.mark.parametrize('error', [RuntimeError, KeyboardInterrupt, RunCancelled])
def test_late_failure_keeps_checked_candidate_and_confirms_draft(rig, monkeypatch, boundary, error):
    state, run, _, _, _, remote_sha, _, _ = rig
    candidates = []
    seen = []
    hooks = dict(candidate_progress=candidates.append, save=state['saved'].append,
                 report=state['reported'].append)

    def fail(*args):
        assert remote_sha('translation')
        seen.append(boundary)
        raise error('late boundary failure')

    if boundary == 'close':
        original = ModelClient.close
        def close(client):
            original(client)
            fail()
        monkeypatch.setattr(ModelClient, 'close', close)
    elif boundary == 'cancel':
        hooks['cancelled'] = lambda: fail() if state['pulls'] else False
    else:
        hooks[boundary] = fail
    result = run(hooks=RunHooks(**hooks))
    assert_retained(result, state, remote_sha, candidates)
    assert result.cancelled == (error is not RuntimeError)
    assert result.publication.draft and state['pulls'][-1][1]['draft']
    assert seen
    if boundary != 'cancel':
        # Only final persistence reconciles a changed result; no model/report retry.
        assert seen == [boundary] * (2 if boundary == 'save' else 1)
    if boundary in {'close', 'save', 'cancel'}:
        assert state['reported'][0].status == 'RED'


@pytest.mark.parametrize('boundary', ['publication', 'save', 'report'])
def test_cooperative_cancel_after_publication_is_finalized(rig, boundary):
    state, run, _, _, _, remote_sha, _, _ = rig
    candidates = []
    def cancel(*args):
        state['cancelled'] = True
    hooks = dict(candidate_progress=candidates.append, save=state['saved'].append,
                 report=state['reported'].append, cancelled=lambda: state['cancelled'])
    if boundary == 'publication':
        state['published_read'] = cancel
    else:
        hooks[boundary] = cancel
    result = run(hooks=RunHooks(**hooks))
    assert_retained(result, state, remote_sha, candidates)
    assert result.cancelled and result.publication.draft
    assert state['pulls'][-1][1]['draft']


@pytest.mark.parametrize('draft_failure', ['exception', 'interrupt', 'unconfirmed'])
def test_failed_draft_conversion_is_explicit_and_never_claimed(rig, monkeypatch, draft_failure):
    state, run, _, _, _, remote_sha, publisher, _ = rig
    candidates = []
    conversions = []
    def convert(*args):
        conversions.append(args)
        if draft_failure != 'unconfirmed':
            raise (KeyboardInterrupt if draft_failure == 'interrupt' else RuntimeError)('draft outage')
    monkeypatch.setattr(publisher.github, 'convert_pull_to_draft', convert)
    def save(result):
        raise RuntimeError('storage outage')
    result = run(hooks=RunHooks(candidate_progress=candidates.append, save=save,
                               report=state['reported'].append))
    assert_retained(result, state, remote_sha, candidates)
    assert not result.publication.draft and not state['pulls'][-1][1]['draft']
    assert any('storage:' in e for e in result.errors)
    assert any('draft:' in e for e in result.errors)
    assert result.cancelled == (draft_failure == 'interrupt')
    assert len(conversions) == 1
    assert len(state['reported']) == 1
    reported = state['reported'][0]
    assert result.errors[-1].startswith('storage final outcome:')
    assert reported.errors == result.errors[:-1]
    assert reported == replace(result, errors=reported.errors, message=reported.message)


def test_publication_recovery_interrupt_is_reported_without_false_draft(rig, monkeypatch):
    state, run, _, _, git, remote_sha, publisher, _ = rig
    def race():
        # The PR is ready, but its head no longer matches the checked candidate.
        git('push', '--force', 'https://x-access-token:dummy@github.com/up/docs.git',
            state['sha'] + ':refs/heads/translation')
    state['published_read'] = race
    def interrupt(*args):
        raise KeyboardInterrupt('draft recovery interrupted')
    monkeypatch.setattr(publisher.github, 'convert_pull_to_draft', interrupt)
    result = run()
    assert result.status == 'RED' and result.cancelled
    assert result.result_sha is None
    assert remote_sha('translation') != result.candidate_sha
    assert not result.publication.draft and not state['pulls'][-1][1]['draft']
    assert any('draft' in e and 'KeyboardInterrupt' in e for e in result.errors)
    assert state['saved'] == state['reported'] == [result]


@pytest.mark.parametrize('error', [RuntimeError, KeyboardInterrupt])
def test_draft_verification_failure_does_not_claim_confirmation(rig, monkeypatch, error):
    state, run, _, _, _, remote_sha, publisher, _ = rig
    candidates = []
    original = publisher.github.convert_pull_to_draft
    def unreadable(*args):
        raise error('draft verification unavailable')
    def convert(*args):
        original(*args)
        monkeypatch.setattr(publisher.github, 'get_pull', unreadable)
    monkeypatch.setattr(publisher.github, 'convert_pull_to_draft', convert)
    def report(result):
        raise RuntimeError('report outage')
    result = run(hooks=RunHooks(candidate_progress=candidates.append, report=report))
    assert_retained(result, state, remote_sha, candidates)
    assert state['pulls'][-1][1]['draft']  # mutation happened, but its readback failed
    assert not result.publication.draft
    assert any('draft: ' in e and 'verification unavailable' in e for e in result.errors)
    assert result.cancelled == (error is KeyboardInterrupt)
