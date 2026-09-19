"""Actual runner, model transport, Git push, YFM build and quality components.

Only external HTTP APIs are mocked. All Git writes target temporary repositories.
"""
# ruff: noqa: RUF001 -- Intentional Russian translation fixtures.
import json
import os
import shlex
import shutil
import subprocess
from dataclasses import replace
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

pytestmark = pytest.mark.timeout(60)

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
            if state.get('deny_fork_access') and path.startswith('/repos/contributor/docs/'):
                return response(dict(message='Fork access denied'), 403)
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


def english_source(data):
    return data['source'].replace('Привет', 'Hello').replace('Привет, мир.', 'Hello world.').replace(
        'Hello, мир.', 'Hello world.')


def assert_inline(result, state, remote_sha):
    assert result.mode == 'doc_verify'
    assert result.publication.pr_number == 1
    assert result.result_sha == result.candidate_sha == remote_sha(state['branch'])
    assert result.publication.base == 'main'
    assert not state['pulls']
    assert remote_sha('translation') is None
    assert all(op in {'critic', 'repair'} for op, _ in state['calls'])
    assert result.cost_breakdown['translation'] == 0
    assert result.files == ()
    assert result.attempts == tuple(state['recorded_attempts'])
    original = Candidate.open(result.candidate.repo, result.snapshot.source_sha)
    selected_paths = {f.path for f in result.selected_files}
    assert set(original.entries) == set(result.candidate.entries)
    for path in original.entries:
        if path not in selected_paths:
            assert result.candidate.read(path) == original.read(path), path
    assert state['saved'] == state['reported'] == [result]


@pytest.mark.parametrize('context', ['ru_only', 'en_only', 'bilingual', 'service'])
def test_current_pairs_noop_exact_sha_no_empty_commit(system, context):
    state, run, commit, repo, git, remote_sha, *_ = system
    commit({'ru/other.md': '# Другая\r\n', 'en/other.md': '# Other\r\n'})
    state['changes'] = [dict(filename=ROOT+lang+'/a.md', status='modified')
                        for lang in (['ru', 'en'] if context == 'bilingual' else
                                     ['en'] if context in {'en_only', 'service'} else ['ru'])]
    # Provenance markers never select another mode or authorize another PR.
    if context == 'service':
        state['branch'] = 'ydbdoc-review/pr-99'
        system[6].branch = state['branch']
        git('push', 'https://x-access-token:dummy@github.com/up/docs.git',
            state['sha']+':refs/heads/'+state['branch'])
        state['metadata'] = dict(title='Translation #99', body='<!-- ydbdoc source_pr=99 -->',
                                 labels=[dict(name='doc_verify')])
    initial = state['sha']
    before = git('rev-list', '--all', '--count')
    index = git('write-tree')
    (repo / ROOT / 'ru/a.md').write_bytes(b'dirty source')
    (repo / ROOT / 'en/a.md').write_bytes(b'dirty target')
    result = run()
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert_inline(result, state, remote_sha)
    assert result.checked_sha == result.candidate_sha == initial
    assert result.snapshot.source_sha == initial
    assert len(result.selected_files) == 1 and result.selected_files[0].target_lang == 'en'
    assert result.candidate.read(ROOT+'ru/a.md') == '# Привет\n\nПривет, мир.\n'.encode()
    assert result.candidate.read(ROOT+'ru/other.md') == '# Другая\r\n'.encode()
    assert result.candidate.read(ROOT+'en/other.md') == b'# Other\r\n'
    assert [op for op, _ in state['calls']] == ['critic']
    assert state['admitted'] == state['made'] == 1
    assert state['calls'][0][1]['source'] == '# Привет\n\nПривет, мир.\n'
    assert result.quality.rounds[0].build.ok_for(initial)
    assert ROOT+'en/a.md' in result.quality.rounds[0].build.anchors
    assert git('rev-list', '--all', '--count') == before
    assert git('write-tree') == index
    assert (repo / ROOT / 'ru/a.md').read_bytes() == b'dirty source'
    assert (repo / ROOT / 'en/a.md').read_bytes() == b'dirty target'
    assert result.cost_breakdown == dict(translation=Decimal(0), critic=Decimal('.25'),
                                         repair=Decimal(0), total=Decimal('.25'))


def test_repair_is_en_only_checked_committed_and_published(system):
    state, run, commit, _, git, remote_sha, *_ = system
    original = '# Привет\r\n\r\nПривет, мир.\r\n'
    commit({'ru/a.md': original, 'en/a.md': '# Привет\r\n\r\nHello world.\r\n',
            'en/other.md': '# Stable\n', 'ru/other.md': '# Стабильно\n'})
    initial = state['sha']
    state['handler'] = lambda op, data: GOOD if op == 'critic' else english_source(data)
    result = run(instruction='Keep the greeting.')
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert_inline(result, state, remote_sha)
    assert result.checked_sha == result.candidate_sha != initial
    assert result.candidate.read(ROOT+'ru/a.md') == original.encode()
    assert result.candidate.text(ROOT+'en/a.md') == '# Hello\r\n\r\nHello world.\r\n'
    assert git('diff', '--name-only', initial, result.candidate_sha).decode().splitlines() == [ROOT+'en/a.md']
    assert [op for op, _ in state['calls']] == ['critic', 'repair', 'critic']
    assert state['calls'][1][1]['instruction'] == 'Keep the greeting.'
    assert state['calls'][1][1]['current_target'].startswith('# Привет')
    assert result.quality.rounds[-1].build.ok_for(result.checked_sha)
    assert len(result.attempts) == len(state['records']) == 3
    assert result.cost_breakdown['total'] == Decimal('.75')


@pytest.mark.parametrize('case', ['empty', 'unpaired', 'deleted', 'acl', 'limit', 'files',
                                  'missing', 'bad_ru', 'bad_en', 'budget', 'new_publisher',
                                  'wrong_pr', 'wrong_branch', 'fork_access_denied', 'merged', 'head_race'])
def test_preflight_before_model_and_no_mutation(system, case):
    state, run, commit, _, git, remote_sha, publisher, settings = system
    kw = {}
    if case == 'empty':
        state['changes'] = []
    elif case == 'unpaired':
        commit({'en/a.md': None})
    elif case == 'deleted':
        commit({'ru/a.md': None})
        state['changes'] = [dict(filename=ROOT+'ru/a.md', status='removed')]
    elif case == 'acl':
        kw['actor'] = 'outsider'
    elif case == 'limit':
        kw['settings'] = replace(settings, max_source_characters=1)
    elif case == 'files':
        state['fail_files'] = True
    elif case == 'missing':
        state['changes'] = [dict(filename=ROOT+'ru/missing.md', status='added')]
    elif case in {'bad_ru', 'bad_en'}:
        commit({case.removeprefix('bad_')+'/a.md': b'\xff'})
    elif case == 'budget':
        def denied():
            raise RuntimeError('YDBDOC_DAILY_BUDGET_RUB exhausted')
        kw['admit'] = denied
    elif case == 'new_publisher':
        publisher.pr_number = None
    elif case == 'wrong_pr':
        publisher.pr_number = 2
    elif case == 'wrong_branch':
        publisher.branch = 'other'
    elif case == 'fork_access_denied':
        state['source_repo'] = 'contributor/docs'
        state['deny_fork_access'] = True
    elif case == 'merged':
        state['merged'] = True
    elif case == 'head_race':
        def raced():
            if state['pull_reads'] == 2:
                commit({'ru/a.md': '# Changed concurrently\n'})
        state['read_hook'] = raced
    result = run(**kw)
    assert result.status == ('NO_WORK' if case in {'empty', 'unpaired', 'deleted'} else 'RED')
    assert result.candidate is None and result.publication is None
    assert not state['calls'] and not state['pulls'] and state['made'] == 0
    assert remote_sha('topic') == state['sha']
    assert git('rev-parse', 'HEAD').decode().strip() == state['sha']
    assert state['saved'] == state['reported'] == [result]
    if case == 'fork_access_denied':
        assert '403' in result.message
    if case in {'bad_ru', 'bad_en', 'missing'}:
        assert '/a.md' in result.message or '/missing.md' in result.message


@pytest.mark.parametrize('failure', ['critic_timeout', 'critic_invalid', 'repair_timeout',
                                     'repair_invalid', 'persistent_quality'])
def test_bounded_errors_remain_red_in_same_pr(system, failure):
    state, run, commit, _, _, remote_sha, *_ = system
    initial = commit({'en/a.md': '# Ошибка\n\nHello world.\n'})
    def handler(op, data):
        if failure == 'critic_timeout' and op == 'critic':
            return requests.exceptions.Timeout('critic unavailable')
        if failure == 'critic_invalid' and op == 'critic':
            return 'not JSON'
        if failure == 'repair_timeout' and op == 'repair':
            return requests.exceptions.Timeout('repair unavailable')
        if failure == 'repair_invalid' and op == 'repair':
            return ''
        return GOOD if op == 'critic' else (
            data['source'] if failure == 'persistent_quality' else english_source(data))
    state['handler'] = handler
    result = run()
    assert result.status == 'RED'
    assert_inline(result, state, remote_sha)
    assert result.publication.draft
    expected = 1 if failure in {'repair_timeout', 'repair_invalid'} else 2
    assert len(result.quality.rounds) == expected
    assert [op for op, _ in state['calls']] == ['critic', 'repair'] * expected
    assert result.checked_sha == result.candidate_sha
    assert result.issues
    assert result.cost_breakdown['total'] == (None if failure.endswith('timeout') else Decimal('.50') * expected)
    assert len(result.attempts) == 2 * expected
    if failure.endswith('timeout'):
        assert any(a.error and a.usage.cost_rub is None for a in result.attempts)
    if failure.startswith('repair_'):
        assert result.unfinished_files == (ROOT+'en/a.md',)
        assert result.candidate_sha == initial


@pytest.mark.parametrize('failure', ['repair_error', 'cancel', 'keyboard', 'candidate_storage'])
def test_partial_results_survive_and_ru_stays_identical(system, failure):
    state, run, commit, _, git, remote_sha, *_ = system
    initial = commit({'en/a.md': '# Ошибка\n\nHello world.\n',
                      'ru/b.md': '# Привет\n', 'en/b.md': '# Ошибка\n'})
    state['changes'].append(dict(filename=ROOT+'en/b.md', status='modified'))
    def handler(op, data):
        if op == 'critic':
            return GOOD
        if data['path'].endswith('/b.md'):
            if failure == 'keyboard':
                return KeyboardInterrupt()
            if failure == 'repair_error':
                return requests.exceptions.Timeout('second file')
        return english_source(data)
    state['handler'] = handler
    candidates = []
    def progress(tree):
        candidates.append(tree.sha)
        if len(candidates) == 2:
            if failure == 'cancel':
                state['cancelled'] = True
            if failure == 'candidate_storage':
                raise RuntimeError('progress unavailable')
    result = run(hooks=RunHooks(candidate_progress=progress, save=state['saved'].append,
                               report=state['reported'].append,
                               cancelled=lambda: state['cancelled']))
    assert result.status == 'RED'
    assert_inline(result, state, remote_sha)
    assert result.candidate.text(ROOT+'en/a.md') == '# Hello\n\nHello world.\n'
    assert result.publication.draft
    assert result.candidate_sha == candidates[-1] != initial
    assert all(p.startswith(ROOT+'en/') for p in
               git('diff', '--name-only', initial, result.candidate_sha).decode().splitlines())
    if failure in {'cancel', 'keyboard'}:
        assert result.cancelled
        assert result.checked_sha != result.candidate_sha
        assert ROOT+'en/b.md' in result.unfinished_files
    if failure == 'repair_error':
        assert result.unfinished_files == (ROOT+'en/b.md',)
    if failure == 'candidate_storage':
        assert any('progress unavailable' in error for error in result.errors)


@pytest.mark.parametrize('failure', ['push', 'race', 'post_push_race', 'storage', 'report'])
def test_publication_and_finalization_failures_are_explicit(system, failure):
    from ydbdoc_review.publication import freeze
    state, run, commit, _, git, remote_sha, *_ = system
    commit({'en/a.md': '# Ошибка\n\nHello world.\n'})
    foreign = []
    def move(tree):
        other = freeze(tree, {'unrelated': b'concurrent'})
        git('push', 'https://x-access-token:dummy@github.com/up/docs.git', other.sha+':refs/heads/topic')
        foreign.append(other.sha)
    def handler(op, data):
        if op == 'repair' and failure == 'race':
            move(Candidate.open(system[3], state['sha']))
        return GOOD if op == 'critic' else english_source(data)
    state['handler'] = handler
    if failure == 'push':
        hooks_dir = state['remote'] / 'hooks'
        hooks_dir.mkdir(exist_ok=True)
        reject = hooks_dir / 'pre-receive'
        reject.write_text('#!/bin/sh\nexit 1\n')
        reject.chmod(0o755)
    if failure == 'post_push_race':
        def raced():
            if state['pull_reads'] == 3:
                move(Candidate.open(system[3], remote_sha('topic')))
        state['read_hook'] = raced
    def unavailable(_):
        raise RuntimeError('adapter unavailable')
    kw = {}
    if failure in {'storage', 'report'}:
        kw['hooks'] = RunHooks(save=unavailable if failure == 'storage' else state['saved'].append,
                               report=unavailable if failure == 'report' else state['reported'].append)
    result = run(**kw)
    assert result.status == 'RED' and result.errors
    assert not state['pulls']
    if failure in {'push', 'race', 'post_push_race'}:
        assert result.result_sha is None
        if foreign:
            assert remote_sha('topic') == foreign[-1] != result.candidate_sha
    else:
        assert result.publication.draft
        assert result.result_sha == result.checked_sha == remote_sha('topic')


def test_renamed_bilingual_pair_at_new_path_and_empty_pair(system):
    state, run, commit, _, _, remote_sha, *_ = system
    commit({'ru/a.md': None, 'en/a.md': None, 'ru/new.md': '# Привет\n',
            'en/new.md': '# Hello\n', 'ru/empty.md': '', 'en/empty.md': '',
            'toc.yaml': 'title: Docs\nitems:\n  - name: New\n    href: en/new.md\n'})
    state['changes'] = [dict(filename=ROOT+lang+'/new.md', status='renamed',
                             previous_filename=ROOT+lang+'/a.md', changes=0, additions=0, deletions=0)
                        for lang in ('ru', 'en')]
    state['changes'].append(dict(filename=ROOT+'en/empty.md', status='added'))
    result = run()
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert_inline(result, state, remote_sha)
    assert {f.path for f in result.selected_files} == {ROOT+'en/new.md', ROOT+'en/empty.md'}
    assert result.candidate_sha == state['sha']
    assert result.candidate.read(ROOT+'ru/empty.md') == b''
    assert result.candidate.read(ROOT+'en/empty.md') == b''
    assert result.candidate.read(ROOT+'en/a.md') is None


def test_glossary_page_is_repairable_with_explicit_glossary(system):
    state, run, commit, _, _, remote_sha, *_ = system
    source = '# Термин\n\nБаза данных.\n'
    commit({'ru/glossary.md': source, 'en/glossary.md': '# Term\n\nStorage.\n'})
    state['changes'] = [dict(filename=ROOT+'en/glossary.md', status='modified')]
    def handler(op, data):
        if op == 'critic':
            return GOOD
        assert data['glossary'] == {'База данных': 'Database'}
        return data['source'].replace('Термин', 'Term').replace('База данных', 'Database')
    state['handler'] = handler
    result = run(glossary=(('База данных', 'Database'),))
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert_inline(result, state, remote_sha)
    assert result.candidate.text(ROOT+'en/glossary.md') == '# Term\n\nDatabase.\n'
    assert result.candidate.read(ROOT+'ru/glossary.md') == source.encode()
    assert [op for op, _ in state['calls']] == ['critic', 'repair', 'critic']


@pytest.mark.parametrize('case', ['url_repair', 'missing_translation', 'whole_tree_asset',
                                  'whole_tree_build', 'protected_warning'])
def test_real_automatic_gates_and_no_dependency_translation(system, case):
    state, run, commit, _, _, remote_sha, *_ = system
    files = {}
    if case in {'url_repair', 'missing_translation'}:
        files.update({'ru/a.md': '# Page\n\n[Link](/ru/b.md)\n',
                      'en/a.md': '# Page\n\n[Link](/ru/b.md)\n', 'ru/b.md': '# B\n'})
        if case == 'url_repair':
            files['en/b.md'] = '# B\n'
    elif case == 'whole_tree_asset':
        files['en/unselected.md'] = '# Broken\n\n![image](missing.png)\n'
    elif case == 'whole_tree_build':
        files['en/unselected.md'] = '# Broken\n\n[link](missing.md)\n'
        files['toc.yaml'] = ('title: Docs\nitems:\n  - name: EN\n    href: en/a.md\n'
                             '  - name: Broken\n    href: en/unselected.md\n')
    else:
        files.update({lang+'/a.md': '# Page\n\n```text\nРусский код\n```\n'
                      for lang in ('ru', 'en')})
    initial = commit(files)
    result = run()
    good = case in {'url_repair', 'protected_warning'}
    assert result.status == ('GREEN' if good else 'RED'), (result.errors, result.issues)
    assert_inline(result, state, remote_sha)
    assert [f.path for f in result.selected_files] == [ROOT+'en/a.md']
    if case == 'url_repair':
        assert result.candidate.text(ROOT+'en/a.md') == '# Page\n\n[Link](/en/b.md)\n'
        assert [op for op, _ in state['calls']] == ['critic', 'repair', 'critic']
        assert result.checked_sha == result.result_sha != initial
    if case == 'missing_translation':
        assert result.candidate.read(ROOT+'en/b.md') is None
        assert any('/ru/b.md' in i.problem for i in result.issues)
    if case in {'whole_tree_asset', 'whole_tree_build'}:
        assert any('unselected.md' in i.path or 'missing' in i.problem for i in result.issues)
        assert result.candidate_sha == initial
    if case == 'whole_tree_build':
        assert not result.quality.rounds[-1].build.ok_for(result.checked_sha)
    if case == 'protected_warning':
        assert result.candidate_sha == initial
        assert any(i.severity == 'warning' for i in result.issues)
        assert [op for op, _ in state['calls']] == ['critic']


def test_single_transport_fallback_and_unknown_timeout_cost_retained(system):
    state, run, _, _, _, remote_sha, *_ = system
    main = Endpoint('eliza', 'https://model.invalid/main', 'main', 'dummy')
    alternate = Endpoint('eliza', 'https://model.invalid/alternate', 'alternate', 'dummy')
    def handler(op, data):
        assert op == 'critic'
        return requests.exceptions.Timeout('main down') if len(state['calls']) == 1 else GOOD
    state['handler'] = handler
    result = run(critic_choice=ModelChoice(main, alternate))
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert_inline(result, state, remote_sha)
    assert len(result.attempts) == len(state['records']) == 2
    assert result.attempts[0].error and result.attempts[0].usage.cost_rub is None
    assert result.attempts[1].usage.cost_rub == Decimal('.25')
    assert result.cost_breakdown['critic'] is None and result.cost_breakdown['total'] is None
    assert state['admitted'] == state['made'] == 1


def test_matching_branch_and_pr_preflight_still_rejects_newer_snapshot(system):
    state, run, commit, *_ = system
    initial = state['sha']
    def move():
        state['branch_hook'] = None
        commit({'ru/a.md': '# Concurrent\n'})
    state['branch_hook'] = move
    result = run()
    assert result.status == 'RED'
    assert 'head changed since the fixed verify snapshot' in result.message
    assert result.snapshot.source_sha == initial != state['sha']
    assert state['made'] == state['admitted'] == 0
    assert not state['calls'] and not state['pulls']
    assert result.candidate is None and result.publication is None


def test_semantic_critic_issue_repairs_existing_en_with_locations(system):
    state, run, commit, _, _, remote_sha, *_ = system
    commit({'en/a.md': '# Hello\n\nGoodbye world.\n'})
    finding = dict(path=ROOT+'en/a.md', problem='Meaning reversed', expected_fix='Restore greeting',
                   severity='error', source=dict(start=3, end=3, quote='Привет, мир.'),
                   target=dict(start=3, end=3, quote='Goodbye world.'))
    def handler(op, data):
        if op == 'critic':
            return json.dumps(dict(complete=True, verdict='issues', issues=[finding])) if (
                len(state['calls']) == 1) else GOOD
        assert any(i['problem'] == 'Meaning reversed' for i in data['findings'])
        return english_source(data)
    state['handler'] = handler
    result = run()
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert_inline(result, state, remote_sha)
    first = result.quality.rounds[0].checks[0]
    issue = next(i for i in first.issues if i.problem == 'Meaning reversed')
    assert issue.target.start == issue.source.start == 3
    assert issue.target.quote == 'Goodbye world.'
    assert result.candidate.text(ROOT+'en/a.md') == '# Hello\n\nHello world.\n'
    assert [op for op, _ in state['calls']] == ['critic', 'repair', 'critic']
