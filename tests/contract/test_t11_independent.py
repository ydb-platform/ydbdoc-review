"""Independent T11 assertions. Audited Git/HTTP fixture adapted from test_verify_t11.
No production component is mocked; only HTTP transport/close failure injection.
"""
# ruff: noqa: RUF001 -- Intentional Russian translation fixtures.
import json
import os
import shlex
import shutil
import subprocess
from decimal import Decimal
from urllib.parse import unquote, urlsplit

import pytest
import requests

from ydbdoc_review.config.loader import Settings
from ydbdoc_review.document import RequestBudget
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.links import Candidate
from ydbdoc_review.model import Endpoint, ModelChoice, ModelClient
from ydbdoc_review.publication import Publisher
from ydbdoc_review.runner import RunHooks
from ydbdoc_review.verify import run_verify

pytestmark = pytest.mark.timeout(120)

ROOT = 'ydb/docs/'
GOOD = json.dumps(dict(complete=True, verdict='correct', issues=[]))


@pytest.fixture
def system(git_repo, tmp_path, monkeypatch):
    repo, git = git_repo
    # Portable Git distribution provides builtins but no receive/upload executables.
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for command in ('receive-pack', 'upload-pack'):
        tool = bin_dir / ('git-' + command)
        tool.write_text('#!/bin/sh\nexec ' + shlex.quote(shutil.which('git'))
                        + ' ' + command + ' "$@"\n')
        tool.chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir) + os.pathsep + os.environ['PATH'])
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
    # Real push helper still builds its production HTTPS URL. Git rewrites it to
    # an offline bare repository; neither push nor subprocess is mocked.
    git('config', f'url.{remote}.insteadOf', 'https://x-access-token:dummy@github.com/up/docs.git')
    state = dict(merged=False, branch='topic', source_repo='up/docs', changes=[], pulls=[], calls=[], records=[], recorded_attempts=[],
                 made=0, admitted=0, saved=[], reported=[], fail_create=False, fail_files=False,
                 handler=None, progress=None, cancelled=False, remote=remote, published_read=None, draft=False, pull_reads=0, read_hook=None)
    def commit(files):
        for path, content in files.items():
            path = repo / ROOT / path
            path.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content.encode() if isinstance(content, str) else content)
        git('add', '.')
        git('commit', '--allow-empty', '-m', 'source fixture')
        sha = git('rev-parse', 'HEAD').decode().strip()
        state['sha'] = sha
        git('push', 'https://x-access-token:dummy@github.com/up/docs.git',
            sha + ':refs/heads/' + ('main' if state['merged'] else state['branch']))
        return sha
    commit({'ru/a.md': '# Привет\n\nПривет, мир.\n',
            'en/a.md': '# Hello\n\nHello world.\n', 'index.md': '# Docs\n',
            'toc.yaml': 'title: Docs\nitems:\n  - name: Home\n    href: index.md\n'
                        '  - name: RU\n    href: ru/a.md\n  - name: EN\n    href: en/a.md\n'})
    def remote_sha(branch):
        proc = subprocess.run(['git', '--git-dir', str(remote), 'rev-parse', '--verify',
                               'refs/heads/' + branch], capture_output=True)
        return proc.stdout.decode().strip() if proc.returncode == 0 else None
    def response(data, status=200):
        r = requests.Response()
        r.status_code = status
        r._content = json.dumps(data).encode()
        return r
    def send(session, req, **kw):
        if 'model.invalid' in req.url:
            session._t11_model_session = True
            record = state['records'][-1]
            content = json.loads(req.body)['messages'][-1]['content']
            op = record.operation
            data = content if op == 'translation' else json.loads(content)
            state['calls'].append((op, data))
            output = (state['handler'](op, data) if state['handler'] else
                      GOOD if op == 'critic' else
                      data.split('\n\n', 1)[1] if op == 'translation' else data['source'])
            if isinstance(output, BaseException):
                raise output
            return response(dict(choices=[dict(message=dict(content=output), finish_reason='stop')],
                                 usage=dict(prompt_tokens=10, completion_tokens=5)))
        path = urlsplit(req.url).path
        state.setdefault('http', []).append((req.method, path))
        assert path == '/graphql' or path.startswith('/repos/up/docs/') or path.startswith('/repos/contributor/docs/git/ref/'), (req.method, path)
        if path.endswith('/pulls/1'):
            state['pull_reads'] += 1
            if state['read_hook']:
                state['read_hook']()
            return response(dict(state='closed' if state['merged'] else 'open',
                                 merged=state['merged'],
                                 head=dict(sha=remote_sha(state['branch']), ref=state['branch'],
                                           repo=dict(full_name=state['source_repo'])),
                                 base=dict(ref='main'), draft=state['draft'], node_id='PR_1',
                                 **state.get('metadata', {})))
        if path.endswith('/pulls/1/files'):
            return response(state['changes'], 503 if state['fail_files'] else 200)
        if '/git/ref/heads/' in path:
            if state.get('branch_hook'):
                state['branch_hook']()
            branch = unquote(path.split('/heads/')[1])
            sha = state.get('base_sha', state['sha']) if branch == 'main' else remote_sha(branch)
            return response(dict(object=dict(sha=sha)) if sha else {}, 200 if sha else 404)
        if path.endswith('/pulls') and req.method == 'POST':
            state['pulls'].append(json.loads(req.body))
            raise AssertionError('verify must never create_pull')
        if path == '/graphql':
            state['draft'] = True
            return response(dict(data=dict(convertPullRequestToDraft=dict(
                pullRequest=dict(isDraft=True)))))
        raise AssertionError((req.method, req.url))
    monkeypatch.setattr(requests.Session, 'send', send)
    github = GitHubClient('dummy')
    publisher = Publisher(github, 'up/docs', 'https://github.com/up/docs.git', 'topic', 'dummy', pr_number=1)
    settings = Settings(20, 250000, frozenset({'writer'}), Decimal(100), 'endpoint', 'db', 'key')
    choice = ModelChoice(Endpoint('eliza', 'https://model.invalid', 'main', 'dummy'))
    def factory():
        state['made'] += 1
        return ModelClient(record_request=state['records'].append, record_attempt=state['recorded_attempts'].append,
                           cost_resolver=lambda ep, data: Decimal('0.25'))
    def admit():
        state['admitted'] += 1
    def progress(file):
        if state['progress']:
            state['progress'](file)
    def run(**kw):
        opts = dict(repo=repo, github=github, owner='up', repository='docs', pr_number=1,
                    actor='writer', settings=settings, publisher=publisher, model_factory=factory,
                    admit=admit, critic_choice=choice,
                    repair_choice=choice, budget=RequestBudget(100000, 20000, lambda m: len(str(m))),
                    hooks=RunHooks(file_progress=progress, save=state['saved'].append,
                                   report=state['reported'].append,
                                   cancelled=lambda: state['cancelled']))
        opts.update(kw)
        return run_verify(**opts)
    state['changes'] = [dict(filename=ROOT+'ru/a.md', status='added')]
    return state, run, commit, repo, git, remote_sha, publisher, settings



def corrected(data):
    return data['source'].replace('Привет, мир.', 'Hello world.').replace('Привет', 'Hello')


def check_receipt(result, system):
    state, _, _, _, _, remote_sha, *_ = system
    assert result.publication.pr_number == 1
    assert result.publication.url == 'https://github.com/up/docs/pull/1'
    assert result.result_sha == result.candidate_sha == remote_sha(state['branch'])
    assert result.files == () and result.cost_breakdown['translation'] == 0
    assert not state['pulls']
    assert all(op in {'critic', 'repair'} for op, _ in state['calls'])
    assert not any(method == 'POST' and path.endswith('/pulls') for method, path in state['http'])


@pytest.mark.parametrize('context', ['ru', 'en', 'bilingual', 'service'])
@pytest.mark.parametrize('repair', [False, True])
def test_same_pr_snapshot_and_en_only(system, context, repair):
    state, run, commit, repo, git, remote_sha, publisher, _ = system
    source = '# Привет\r\n\r\nПривет, мир.\r\n'
    initial = commit({'ru/a.md': source, 'en/a.md': '# Ошибка\n\nHello world.\n' if repair else '# Hello\r\n\r\nHello world.\r\n',
                      'en/stable.md': b'# Stable\r\n', 'ru/stable.md': b'# Stable RU\r\n'})
    state['changes'] = [dict(filename=ROOT+lang+'/a.md', status='modified')
                        for lang in (['ru', 'en'] if context == 'bilingual' else ['en'] if context == 'service' else [context])]
    if context == 'service':
        state['branch'] = publisher.branch = 'ydbdoc-review/pr-99'
        git('push', 'https://x-access-token:dummy@github.com/up/docs.git', initial+':refs/heads/'+state['branch'])
        state['metadata'] = dict(title='Translation #99', body='<!-- ydbdoc source_pr=99 -->')
    original = Candidate.open(repo, initial)
    count = git('rev-list', '--all', '--count')
    (repo / ROOT / 'ru/a.md').write_bytes(b'dirty RU must not be read')
    state['handler'] = lambda op, data: GOOD if op == 'critic' else corrected(data)
    result = run()
    assert result.status == 'GREEN', result.errors
    check_receipt(result, system)
    assert result.checked_sha == result.result_sha
    assert result.snapshot.source_sha == initial
    assert len(result.selected_files) == 1
    assert state['admitted'] == state['made'] == 1
    for path in original.entries:
        if path != ROOT+'en/a.md':
            assert original.read(path) == result.candidate.read(path), path
    assert state['calls'][0][1]['source'] == source
    assert state['saved'] == state['reported'] == [result]
    assert result.quality.rounds[-1].build.ok_for(result.checked_sha)
    assert ROOT+'en/a.md' in result.quality.rounds[-1].build.anchors
    assert [op for op, _ in state['calls']] == (['critic', 'repair', 'critic'] if repair else ['critic'])
    assert git('diff', '--name-only', initial, result.result_sha).decode().splitlines() == ([ROOT+'en/a.md'] if repair else [])
    if not repair:
        assert initial == result.result_sha
        assert git('rev-list', '--all', '--count') == count
    assert (repo / ROOT / 'ru/a.md').read_bytes() == b'dirty RU must not be read'
    assert remote_sha('verify-1') is None


def test_bounded_persistent_problem(system):
    state, run, commit, *_ = system
    commit({'en/a.md': '# Ошибка\n\nHello world.\n'})
    state['handler'] = lambda op, data: GOOD if op == 'critic' else data['source']
    result = run()
    assert result.status == 'RED'
    check_receipt(result, system)
    assert [op for op, _ in state['calls']] == ['critic', 'repair', 'critic', 'repair', 'critic']
    assert len(result.quality.rounds) == 3 and result.publication.draft
    assert state['reported'][-1].checked_sha == result.result_sha


@pytest.mark.parametrize('stage', ['preflight', 'during_repair'])
def test_exact_head_guard_rejects_concurrent_commit(system, stage):
    state, run, commit, repo, git, remote_sha, *_ = system
    from ydbdoc_review.publication import freeze
    initial = commit({'en/a.md': '# Ошибка\n\nHello world.\n'})
    def move():
        other = freeze(Candidate.open(repo, initial), {'unrelated': b'other user'})
        git('push', 'https://x-access-token:dummy@github.com/up/docs.git', other.sha+':refs/heads/topic')
        state['foreign'] = other.sha
    if stage == 'preflight':
        def branch_hook():
            state['branch_hook'] = None
            move()
        state['branch_hook'] = branch_hook
    else:
        def handler(op, data):
            if op == 'repair':
                move()
            return GOOD if op == 'critic' else corrected(data)
        state['handler'] = handler
    result = run()
    assert result.status == 'RED' and result.result_sha is None
    assert result.snapshot.source_sha == initial
    assert remote_sha('topic') == state['foreign']
    assert not state['pulls']
    if stage == 'preflight':
        assert state['made'] == 0
    else:
        assert result.candidate.text(ROOT+'en/a.md').startswith('# Hello')
        assert state['saved'][-1].candidate_sha == result.candidate_sha


@pytest.mark.parametrize('failure', ['cancel', 'keyboard', 'timeout'])
def test_available_first_repair_survives_second_file_failure(system, failure):
    state, run, commit, _, _, remote_sha, *_ = system
    commit({'en/a.md': '# Ошибка\n\nHello world.\n', 'ru/b.md': '# Привет\n', 'en/b.md': '# Ошибка\n'})
    state['changes'].append(dict(filename=ROOT+'en/b.md', status='modified'))
    def handler(op, data):
        if op == 'critic':
            return GOOD
        if data['path'].endswith('/b.md'):
            if failure == 'keyboard':
                return KeyboardInterrupt('second repair interrupted')
            if failure == 'timeout':
                return requests.exceptions.Timeout('second repair timeout')
        return corrected(data)
    state['handler'] = handler
    retained = []
    def progress(tree):
        retained.append(tree)
        if failure == 'cancel' and len(retained) == 2:
            state['cancelled'] = True
    result = run(hooks=RunHooks(candidate_progress=progress, save=state['saved'].append,
                               report=state['reported'].append, cancelled=lambda: state['cancelled']))
    assert result.status == 'RED'
    check_receipt(result, system)
    assert result.candidate.text(ROOT+'en/a.md') == '# Hello\n\nHello world.\n'
    assert ROOT+'en/b.md' in result.unfinished_files
    assert state['saved'] == state['reported'] == [result]
    assert result.publication.draft
    if failure != 'timeout':
        assert result.cancelled and result.checked_sha != remote_sha('topic')


@pytest.mark.parametrize('stage', ['close_error', 'close_keyboard', 'save_keyboard', 'report_keyboard'])
def test_finishing_failure_preserves_available_result(system, monkeypatch, stage):
    state, run, commit, _, _, remote_sha, *_ = system
    commit({'en/a.md': '# Ошибка\n\nHello world.\n'})
    state['handler'] = lambda op, data: GOOD if op == 'critic' else corrected(data)
    def fail(_):
        if stage == 'close_error':
            raise RuntimeError('injected close failure')
        raise KeyboardInterrupt('injected finishing interruption')
    if stage.startswith('close'):
        original_close = requests.Session.close
        def close(session):
            original_close(session)
            if getattr(session, '_t11_model_session', False):
                fail(session)
        monkeypatch.setattr(requests.Session, 'close', close)
    hooks = RunHooks(save=fail if stage == 'save_keyboard' else state['saved'].append,
                     report=fail if stage == 'report_keyboard' else state['reported'].append)
    try:
        result = run(hooks=hooks)
    except KeyboardInterrupt:
        pytest.fail('KeyboardInterrupt escaped run_verify before preservation/report completion')
    assert result.status == 'RED'
    check_receipt(result, system)
    assert result.candidate.text(ROOT+'en/a.md').startswith('# Hello')
    assert result.publication.draft
    assert result.checked_sha == remote_sha('topic')
    if stage != 'close_error':
        assert result.cancelled
    if stage != 'report_keyboard':
        assert state['reported'][-1].status == 'RED'
    if stage == 'close_keyboard':
        assert state['saved'][-1].cancelled


@pytest.mark.parametrize('publisher_identity', ['upstream', 'fork'])
def test_normal_fork_pr_not_excluded_by_product_contract(system, publisher_identity):
    state, run, commit, _, git, _, publisher, _ = system
    commit({'en/a.md': '# Ошибка\n\nHello world.\n'})
    state['source_repo'] = 'contributor/docs'
    # PR #1 belongs to up/docs; only head/ref belongs to contributor/docs.
    # Local remote is writable; HTTP mock forbids any fork /pulls/1 request.
    git('config', '--add', f"url.{state['remote']}.insteadOf", 'https://x-access-token:dummy@github.com/contributor/docs.git')
    if publisher_identity == 'fork':
        publisher.repository = 'contributor/docs'
        publisher.remote_url = 'https://github.com/contributor/docs.git'
    state['handler'] = lambda op, data: GOOD if op == 'critic' else corrected(data)
    result = run()
    assert result.status == 'GREEN', (
        '§4 has no fork exclusion; separate upstream PR identity from fork push identity', result.errors)
    check_receipt(result, system)
    assert state['made'] == 1


def test_merged_rejection_characterization_not_product_acceptance(system):
    state, run, commit, *_ = system
    state['merged'] = True
    base = commit({'en/a.md': '# Hello\n\nHello world.\n'})
    result = run()
    assert result.snapshot.source_sha == base
    assert result.status == 'RED' and state['made'] == 0
    assert 'requires an open PR' in result.message
    assert state['reported'] == [result]
    # This only characterizes the unconditional rejection; merged semantics
    # need clarification between §1 base snapshot and §4 same-PR repair.


def test_interrupt_retains_last_completed_checked_sha_for_report(system):
    state, run, commit, *_ = system
    initial = commit({'en/a.md': '# Ошибка\n\nHello world.\n'})
    def handler(op, data):
        if op == 'critic':
            if sum(name == 'critic' for name, _ in state['calls']) == 2:
                return KeyboardInterrupt('second check interrupted')
            return GOOD
        return corrected(data)
    state['handler'] = handler
    result = run()
    assert result.status == 'RED' and result.cancelled
    check_receipt(result, system)
    assert result.candidate_sha != initial
    assert [op for op, _ in state['calls']] == ['critic', 'repair', 'critic']
    # First round completed (it found Cyrillic), second candidate is unverified.
    # §6 asks for the LAST checked SHA, not a false check of the latest repair.
    assert result.checked_sha == initial, 'last completed check SHA lost across KeyboardInterrupt'
    assert state['reported'][-1].checked_sha == initial


def test_publication_recovery_interrupt_keeps_cancelled_flag(system, monkeypatch):
    state, run, commit, repo, git, remote_sha, *_ = system
    from ydbdoc_review.publication import freeze
    initial = commit({'en/a.md': '# Ошибка\n\nHello world.\n'})
    state['handler'] = lambda op, data: GOOD if op == 'critic' else corrected(data)
    transport = requests.Session.send
    interrupted = []
    def send(session, req, **kwargs):
        if urlsplit(req.url).path == '/graphql' and not interrupted:
            interrupted.append(True)
            raise KeyboardInterrupt('draft recovery interrupted')
        return transport(session, req, **kwargs)
    monkeypatch.setattr(requests.Session, 'send', send)
    def race():
        if state['pull_reads'] == 3:
            other = freeze(Candidate.open(repo, remote_sha('topic')), {'concurrent': b'after push'})
            git('push', 'https://x-access-token:dummy@github.com/up/docs.git', other.sha+':refs/heads/topic')
    state['read_hook'] = race
    result = run()
    assert interrupted and result.status == 'RED'
    assert result.result_sha is None and result.candidate_sha != initial
    assert any('KeyboardInterrupt' in e for e in result.errors)
    assert state['saved'] == state['reported'] == [result]
    assert result.cancelled, 'verify ignores PublicationError.cancelled from interrupted recovery'
