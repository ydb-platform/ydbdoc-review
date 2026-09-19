"""Independent T11 reacceptance: real isolated Git remotes, mocked HTTP only."""
# ruff: noqa: RUF001 — deliberate Russian source and invalid EN fixtures.
import json
import subprocess
from urllib.parse import urlsplit

import pytest
import requests

from tests.contract import test_t11_independent as original
from ydbdoc_review.links import Candidate
from ydbdoc_review.publication import freeze

system = original.system
ROOT = original.ROOT
pytestmark = pytest.mark.timeout(120)


@pytest.mark.parametrize('identity', ['upstream', 'fork'])
@pytest.mark.parametrize('scenario', ['noop', 'repair', 'red', 'fork_race',
                                     'preflight_sha', 'readback_repo', 'readback_ref',
                                     'readback_sha', 'readback_base'])
def test_two_bare_destinations(system, tmp_path, monkeypatch, identity, scenario):
    state, run, commit, repo, git, upstream_sha, publisher, _ = system
    initial = commit({'ru/a.md': '# Привет\r\n\r\nПривет, мир.\r\n',
                      'en/a.md': '# Hello\n\nHello world.\n' if scenario == 'noop'
                      else '# Ошибка\n\nHello world.\n'})
    original_tree = Candidate.open(repo, initial)
    fork = tmp_path / 'distinct-fork.git'
    subprocess.run(['git', 'init', '--bare', str(fork)], check=True, capture_output=True)
    fork_url = 'https://x-access-token:dummy@github.com/contributor/docs.git'
    git('config', f'url.{fork}.insteadOf', fork_url)
    git('push', fork_url, initial + ':refs/heads/topic')
    state['source_repo'] = 'contributor/docs'
    if identity == 'fork':
        publisher.repository = 'contributor/docs'
        publisher.remote_url = 'https://github.com/contributor/docs.git'
    input_identity = vars(publisher).copy()

    def remote_arg(*args):
        return subprocess.check_output(['git', '--git-dir', str(fork), *args]).decode().strip()

    def fork_sha():
        return remote_arg('rev-parse', 'refs/heads/topic')

    initial_count = remote_arg('rev-list', '--all', '--count')
    transport = requests.Session.send
    graphql = []

    def send(session, request, **kwargs):
        path = urlsplit(request.url).path
        if path == '/graphql':
            graphql.append(json.loads(request.body))
        response = transport(session, request, **kwargs)
        if path == '/repos/up/docs/pulls/1':
            data = response.json()
            data['node_id'] = 'UPSTREAM_ONLY_NODE'
            data['head']['sha'] = fork_sha()
            if scenario == 'preflight_sha' and state['pull_reads'] == 2:
                data['head']['sha'] = '0' * 40
            if scenario.startswith('readback_') and state['pull_reads'] >= 3:
                field = scenario.removeprefix('readback_')
                if field == 'repo':
                    data['head']['repo']['full_name'] = 'someone/else'
                elif field == 'base':
                    data['base']['ref'] = 'other-base'
                else:
                    data['head'][field] = '0' * 40 if field == 'sha' else 'other-branch'
            response._content = json.dumps(data).encode()
        elif path == '/repos/contributor/docs/git/ref/heads/topic':
            response._content = json.dumps({'object': {'sha': fork_sha()}}).encode()
        return response

    monkeypatch.setattr(requests.Session, 'send', send)
    foreign = []

    def handler(op, data):
        if op == 'repair' and scenario == 'fork_race':
            other = freeze(original_tree, {'foreign.txt': b'concurrent fork commit'})
            git('push', fork_url, other.sha + ':refs/heads/topic')
            foreign.append(other.sha)
        if op == 'critic':
            return original.GOOD
        return data['source'] if scenario == 'red' else original.corrected(data)

    state['handler'] = handler
    result = run()
    assert vars(publisher) == input_identity
    assert upstream_sha('topic') == initial
    assert state['saved'] == state['reported'] == [result]
    assert not state['pulls']
    assert all(op in {'critic', 'repair'} for op, _ in state['calls'])
    assert result.cost_breakdown['translation'] == 0
    assert not any('/repos/contributor/docs/pulls' in path for _, path in state['http'])
    if scenario == 'preflight_sha':
        assert result.status == 'RED' and state['made'] == 0
        assert result.result_sha is None and fork_sha() == initial
        return
    for path in original_tree.entries:
        if path != ROOT + 'en/a.md':
            assert result.candidate.read(path) == original_tree.read(path)
    if scenario == 'fork_race':
        assert result.status == 'RED' and result.result_sha is None
        assert fork_sha() == foreign[0]
        assert result.candidate.text(ROOT + 'en/a.md').startswith('# Hello')
        return
    assert result.publication.repository == 'up/docs'
    assert result.publication.url == 'https://github.com/up/docs/pull/1'
    assert result.publication.pr_number == 1
    # This is the identity supplied to report/comment adapters, not a live comment.
    assert state['reported'][0].publication.repository == 'up/docs'
    if scenario.startswith('readback_'):
        assert result.status == 'RED' and result.result_sha is None
        assert not result.publication.head_confirmed
        assert fork_sha() == result.candidate_sha
    else:
        assert result.result_sha == result.checked_sha == fork_sha()
        assert result.status == ('RED' if scenario == 'red' else 'GREEN')
    if scenario == 'red' or scenario.startswith('readback_'):
        assert result.publication.draft and graphql
        assert all('UPSTREAM_ONLY_NODE' in json.dumps(body) for body in graphql)
    if scenario == 'noop':
        assert fork_sha() == initial
        assert remote_arg('rev-list', '--all', '--count') == initial_count
        assert [op for op, _ in state['calls']] == ['critic']
    if scenario == 'red':
        assert [op for op, _ in state['calls']] == ['critic', 'repair', 'critic', 'repair']
        assert len(result.quality.rounds) == 2  # Changed once, then no-op (§5.1)


@pytest.mark.parametrize('boundary', ['second_repair', 'second_build'])
def test_partial_loop_state_across_interrupt(system, boundary):
    from ydbdoc_review.build import build_candidate

    state, run, commit, _, _, remote_sha, *_ = system
    initial = commit({'en/a.md': '# Ошибка\n\nHello world.\n',
                      'ru/b.md': '# Привет\n', 'en/b.md': '# Ошибка\n'})
    state['changes'].append(dict(filename=ROOT + 'en/b.md', status='modified'))
    builds = []

    def build(tree):
        builds.append(tree.sha)
        if boundary == 'second_build' and len(builds) == 2:
            raise KeyboardInterrupt('next build interrupted')
        return build_candidate(tree)

    def handler(op, data):
        if boundary == 'second_repair' and op == 'repair' and data['path'].endswith('/b.md'):
            return KeyboardInterrupt('second file repair interrupted')
        return original.GOOD if op == 'critic' else original.corrected(data)

    state['handler'] = handler
    result = run(build=build)
    assert result.status == 'RED' and result.cancelled and result.publication.draft
    assert result.checked_sha == result.quality.checked_sha == initial
    assert result.candidate_sha == result.quality.candidate.sha == remote_sha('topic') != initial
    assert result.candidate.text(ROOT + 'en/a.md') == '# Hello\n\nHello world.\n'
    assert ROOT + 'en/b.md' in result.unfinished_files
    trace = result.quality.rounds[0]
    assert trace.build.ok_for(initial) and len(trace.checks) == 2
    assert trace.repairs[0].complete and trace.repairs[0].path == ROOT + 'en/a.md'
    assert len(trace.repairs) == (1 if boundary == 'second_repair' else 2)
    assert len(result.quality.rounds) == (1 if boundary == 'second_repair' else 2)
    assert [op for op, _ in state['calls']] == ['critic', 'critic', 'repair', 'repair']
    assert state['saved'] == state['reported'] == [result]
    assert not state['pulls']
