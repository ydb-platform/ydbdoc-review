"""T11 fixes: distinct fork storage and interrupted round evidence, offline only."""
import json
import subprocess
from urllib.parse import urlsplit

import pytest
import requests

from tests.contract import test_t11_independent as independent
from tests.contract.test_t11_independent import GOOD, ROOT, check_receipt, corrected
from ydbdoc_review.links import Candidate
from ydbdoc_review.publication import freeze

pytestmark = pytest.mark.timeout(120)
system = independent.system


@pytest.mark.parametrize('race', [False, True])
def test_fork_push_does_not_touch_upstream_same_named_branch(system, tmp_path, monkeypatch, race):
    state, run, commit, repo, git, upstream_sha, publisher, _ = system
    initial = commit({'en/a.md': '# Ошибка\n\nHello world.\n'})
    fork = tmp_path / 'fork.git'
    subprocess.run(['git', 'init', '--bare', str(fork)], check=True, capture_output=True)
    fork_url = 'https://x-access-token:dummy@github.com/contributor/docs.git'
    git('config', f'url.{fork}.insteadOf', fork_url)
    git('push', fork_url, initial + ':refs/heads/topic')
    state['source_repo'] = 'contributor/docs'

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
    foreign = None

    def handler(operation, data):
        nonlocal foreign
        if operation == 'repair' and race:
            foreign = freeze(Candidate.open(repo, initial), {'foreign': b'fork owner commit'})
            git('push', fork_url, foreign.sha + ':refs/heads/topic')
        return GOOD if operation == 'critic' else corrected(data)

    state['handler'] = handler
    result = run()
    assert upstream_sha('topic') == initial
    assert not state['pulls']
    assert publisher.repository == 'up/docs'  # input configuration is not mutated
    assert result.candidate.text(ROOT + 'en/a.md').startswith('# Hello')
    if race:
        assert result.status == 'RED' and result.result_sha is None
        assert fork_sha() == foreign.sha
    else:
        assert result.status == 'GREEN', result.errors
        assert result.result_sha == result.checked_sha == fork_sha() != initial
        assert result.publication.repository == 'up/docs'
        assert result.publication.url == 'https://github.com/up/docs/pull/1'
    assert state['saved'] == state['reported'] == [result]


@pytest.mark.parametrize('interrupted_operation', ['critic', 'repair'])
def test_interruption_retains_completed_round_evidence(system, interrupted_operation):
    state, run, commit, *_ = system
    initial = commit({'en/a.md': '# Ошибка\n\nHello world.\n'})

    def handler(operation, data):
        count = sum(op == operation for op, _ in state['calls'])
        if operation == interrupted_operation and count == (2 if operation == 'critic' else 1):
            return KeyboardInterrupt('retain round evidence')
        return GOOD if operation == 'critic' else corrected(data)

    state['handler'] = handler
    result = run()
    assert result.status == 'RED' and result.cancelled
    check_receipt(result, system)
    assert result.quality.checked_sha == result.checked_sha == initial
    assert result.quality.rounds[0].candidate_sha == initial
    assert result.quality.rounds[0].build.ok_for(initial)
    assert result.quality.rounds[0].checks
    assert ROOT + 'en/a.md' in result.unfinished_files
    if interrupted_operation == 'critic':
        assert result.candidate_sha != initial
        assert len(result.quality.rounds) == 2
        assert result.quality.rounds[0].repairs[0].complete
        assert result.quality.rounds[1].candidate_sha == result.candidate_sha
    else:
        assert result.candidate_sha == initial
        assert len(result.quality.rounds) == 1
