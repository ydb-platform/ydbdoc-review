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
from urllib.parse import urlsplit

import pytest
import requests

from ydbdoc_review.config.loader import Settings
from ydbdoc_review.document import RequestBudget
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.model import Endpoint, ModelChoice, ModelClient
from ydbdoc_review.publication import Publisher
from ydbdoc_review.runner import RunHooks, run_translate

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
    state = dict(merged=False, source_repo='up/docs', changes=[], pulls=[], calls=[], records=[],
                 made=0, admitted=0, saved=[], reported=[], fail_create=False, fail_files=False,
                 handler=None, progress=None, cancelled=False, remote=remote, published_read=None)
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
            sha + ':refs/heads/' + ('main' if state['merged'] else 'topic'))
        return sha
    commit({'ru/a.md': '# Hello\n\nHello world.\n', 'index.md': '# Docs\n',
            'toc.yaml': 'title: Docs\nitems:\n  - name: Home\n    href: index.md\n'})
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
            return response(dict(state='closed' if state['merged'] else 'open',
                                 merged=state['merged'],
                                 head=dict(sha=state['sha'], ref='topic',
                                           repo=dict(full_name=state['source_repo'])),
                                 base=dict(ref='main')))
        if path.endswith('/pulls/1/files'):
            return response(state['changes'], 503 if state['fail_files'] else 200)
        if '/git/ref/heads/' in path:
            branch = path.split('/heads/')[1]
            sha = state.get('base_sha', state['sha']) if branch == 'main' else remote_sha(branch)
            return response(dict(object=dict(sha=sha)) if sha else {}, 200 if sha else 404)
        if path.endswith('/pulls') and req.method == 'GET':
            return response([])
        if path.endswith('/pulls') and req.method == 'POST':
            if state['fail_create']:
                return response({'error': 'denied'}, 503)
            data = json.loads(req.body)
            state['pulls'].append((path, data))
            return response(dict(html_url='https://github.com/up/docs/pull/2', number=2))
        if path == '/graphql':
            state['pulls'][-1][1]['draft'] = True
            return response(dict(data=dict(convertPullRequestToDraft=dict(
                pullRequest=dict(isDraft=True)))))
        if path.endswith('/pulls/2'):
            if state['published_read']:
                callback, state['published_read'] = state['published_read'], None
                callback()
            body = state['pulls'][-1][1]
            return response(dict(head=dict(sha=remote_sha(body['head']), ref=body['head'],
                                           repo=dict(full_name=state['source_repo'])),
                                 base=dict(ref=body['base']), draft=body['draft'], node_id='PR_2'))
        raise AssertionError((req.method, req.url))
    monkeypatch.setattr(requests.Session, 'send', send)
    github = GitHubClient('dummy')
    publisher = Publisher(github, 'up/docs', 'https://github.com/up/docs.git', 'translation', 'dummy')
    settings = Settings(20, 250000, frozenset({'writer'}), Decimal(100), 'endpoint', 'db', 'key')
    choice = ModelChoice(Endpoint('eliza', 'https://model.invalid', 'main', 'dummy'))
    def factory():
        state['made'] += 1
        return ModelClient(record_request=state['records'].append, record_attempt=lambda r: None,
                           cost_resolver=lambda ep, data: Decimal('0.25'))
    def admit():
        state['admitted'] += 1
    def progress(file):
        if state['progress']:
            state['progress'](file)
    def run(**kw):
        opts = dict(repo=repo, github=github, owner='up', repository='docs', pr_number=1,
                    actor='writer', settings=settings, publisher=publisher, model_factory=factory,
                    admit=admit, translation_choice=choice, critic_choice=choice,
                    repair_choice=choice, budget=RequestBudget(100000, 20000, lambda m: len(str(m))),
                    hooks=RunHooks(file_progress=progress, save=state['saved'].append,
                                   report=state['reported'].append,
                                   cancelled=lambda: state['cancelled']))
        opts.update(kw)
        return run_translate(**opts)
    state['changes'] = [dict(filename=ROOT+'ru/a.md', status='added')]
    return state, run, commit, repo, git, remote_sha, publisher, settings


def test_real_end_to_end_green_and_exact_sha(system):
    state, run, _, repo, git, remote_sha, *_ = system
    source_sha = state['sha']
    (repo / ROOT / 'ru/a.md').write_text('Dirty worktree must not translate')
    result = run()
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert result.checked_sha == result.candidate_sha == remote_sha('translation')
    assert result.candidate.text(ROOT+'en/a.md') == '# Hello\n\nHello world.\n'
    assert git('rev-parse', result.candidate_sha+'^').decode().strip() == source_sha
    assert git('rev-parse', 'HEAD').decode().strip() == source_sha
    assert (repo / ROOT / 'ru/a.md').read_text() == 'Dirty worktree must not translate'
    assert state['pulls'][0][1]['base'] == 'topic'
    assert not state['pulls'][0][1]['draft']
    assert [op for op, _ in state['calls']] == ['translation', 'critic']
    assert state['admitted'] == state['made'] == 1
    assert result.cost_breakdown == dict(translation=Decimal('.25'), critic=Decimal('.25'),
                                         repair=Decimal(0), total=Decimal('.50'))
    assert state['saved'] == state['reported'] == [result]
    assert result.quality.rounds[0].build.ok_for(result.checked_sha)


@pytest.mark.parametrize('case', ['no_work', 'bilingual', 'acl', 'limit', 'files_error', 'missing',
                                  'budget', 'fork'])
def test_preflight_no_client_no_pr(system, case):
    state, run, _, _, _, remote_sha, _, settings = system
    kw = {}
    if case == 'no_work':
        state['changes'] = []
    if case == 'bilingual':
        state['changes'].append(dict(filename=ROOT+'en/a.md', status='modified'))
    if case == 'acl':
        kw['actor'] = 'outsider'
    if case == 'limit':
        kw['settings'] = replace(settings, max_source_characters=1)
    if case == 'files_error':
        state['fail_files'] = True
    if case == 'missing':
        state['changes'] = [dict(filename=ROOT+'ru/missing.md', status='added')]
    if case == 'budget':
        def denied():
            raise RuntimeError('Daily budget exhausted')
        kw['admit'] = denied
    if case == 'fork':
        state['source_repo'] = 'contributor/docs'
    result = run(**kw)
    assert result.status == ('NO_WORK' if case in {'no_work', 'bilingual'} else 'RED')
    assert state['made'] == 0 and not state['calls'] and not state['pulls']
    assert remote_sha('translation') is None
    assert result.candidate is None
    assert state['saved'] == [result]


@pytest.mark.parametrize('case', ['delete', 'rename', 'rename_edit', 'modify'])
def test_mirror_operations(system, case):
    state, run, commit, _, _, _, *_ = system
    commit({'en/a.md': '# Old translation\n', 'ru/b.md': '# Hello\n',
            'ru/a.md': None if case in {'delete', 'rename', 'rename_edit'} else '# Updated\n'})
    if case in {'rename', 'rename_edit'}:
        state['changes'] = [dict(filename=ROOT+'ru/b.md', status='renamed',
                                 previous_filename=ROOT+'ru/a.md',
                                 changes=2 if case == 'rename_edit' else 0,
                                 additions=1 if case == 'rename_edit' else 0,
                                 deletions=1 if case == 'rename_edit' else 0)]
    else:
        state['changes'] = [dict(filename=ROOT+'ru/a.md', status='removed' if case == 'delete'
                                 else 'modified')]
    result = run()
    assert result.status == 'GREEN', result.errors
    if case in {'delete', 'rename', 'rename_edit'}:
        assert result.candidate.read(ROOT+'en/a.md') is None
    if case == 'rename':
        assert result.candidate.text(ROOT+'en/b.md') == '# Old translation\n'
    if case == 'rename_edit':
        assert result.candidate.text(ROOT+'en/b.md') == '# Hello\n'
    if case == 'modify':
        assert result.candidate.text(ROOT+'en/a.md') == '# Updated\n'
        assert 'Old translation' not in state['calls'][0][1]
    assert state['made'] == (0 if case in {'delete', 'rename'} else 1)
    assert state['admitted'] == state['made']
    assert result.publication is not None


def test_merged_current_base_and_assets(system):
    state, run, commit, _, git, *_ = system
    state['merged'] = True
    old = state['sha']
    current = commit({'ru/a.md': '# New\n\n![pic](pic.svg)\n',
                      'ru/pic.svg': b'<svg xmlns="http://www.w3.org/2000/svg"><path id="x"/></svg>'})
    state.update(sha=old, base_sha=current)
    result = run()
    assert result.status == 'GREEN', result.issues
    assert result.snapshot.source_sha == current
    assert git('rev-parse', result.candidate_sha+'^').decode().strip() == current
    assert result.candidate.read(ROOT+'en/pic.svg') == result.candidate.read(ROOT+'ru/pic.svg')
    assert state['pulls'][0][1]['base'] == 'main'


@pytest.mark.parametrize('failure', ['structural', 'one_file', 'cancel', 'keyboard', 'storage_progress'])
def test_partial_red_published_and_unfinished_retained(system, failure):
    state, run, commit, _, _, remote_sha, *_ = system
    commit({'ru/b.md': '# Second\n'})
    state['changes'].append(dict(filename=ROOT+'ru/b.md', status='added'))
    def handler(op, data):
        if op == 'translation':
            if '/en/b.md' in data:
                if failure == 'one_file':
                    return requests.exceptions.Timeout('second file failed')
                if failure == 'keyboard':
                    return KeyboardInterrupt()
            return 'Damaged' if failure == 'structural' else data.split('\n\n', 1)[1]
        return GOOD if op == 'critic' else 'Damaged'
    state['handler'] = handler
    def progress(file):
        if file.path.endswith('/a.md'):
            if failure == 'cancel':
                state['cancelled'] = True
            if failure == 'storage_progress':
                raise RuntimeError('storage progress failed')
    state['progress'] = progress
    result = run()
    assert result.status == 'RED'
    assert result.candidate.text(ROOT+'en/a.md') is not None
    assert remote_sha('translation') == result.candidate_sha
    assert state['pulls'][0][1]['draft']
    assert result.publication.url
    if failure != 'structural':
        assert ROOT+'en/b.md' in result.unfinished_files
    if failure in {'cancel', 'keyboard'}:
        assert result.cancelled
    if failure == 'structural':
        assert len(result.quality.rounds) == 1  # §5.1: repair returned unchanged text
        assert result.checked_sha == result.candidate_sha
    assert state['saved'] == state['reported'] == [result]


@pytest.mark.parametrize('failure', ['push', 'create', 'race'])
def test_publication_failure_is_not_success(system, failure):
    state, run, _, _, git, remote_sha, _publisher, _ = system
    if failure == 'create':
        state['fail_create'] = True
    if failure == 'push':
        hook = state['remote'] / 'hooks' / 'pre-receive'
        hook.parent.mkdir(exist_ok=True)
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
    if failure == 'race':
        def progress(file):
            git('push', 'https://x-access-token:dummy@github.com/up/docs.git',
                state['sha']+':refs/heads/translation')
        state['progress'] = progress
    result = run()
    assert result.status == 'RED' and result.errors
    assert result.candidate.text(ROOT+'en/a.md') is not None
    if failure == 'create':
        assert result.publication.pushed_sha == result.candidate_sha
        assert result.publication.pr_number is None
    else:
        assert result.publication is None and not state['pulls']
    if failure == 'race':
        assert remote_sha('translation') == state['sha'] != result.candidate_sha


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_dependency_closure_both_directions_and_skipped_pair(system, language):
    state, run, commit, *_ = system
    other = 'en' if language == 'ru' else 'ru'
    commit({f'{language}/a.md': '# Article\n\n[dep](dep.md) [skip](skip.md)\n',
            f'{language}/dep.md': '# Dependency\n', f'{language}/skip.md': '# Source skip\n',
            f'{other}/skip.md': '# Target skip\n'})
    state['changes'] = [dict(filename=ROOT+f'{language}/a.md', status='modified'),
                        dict(filename=ROOT+f'{language}/skip.md', status='modified'),
                        dict(filename=ROOT+f'{other}/skip.md', status='modified')]
    result = run()
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert result.plan.dependencies == [ROOT+f'{language}/dep.md']
    assert result.candidate.text(ROOT+f'{other}/dep.md') == '# Dependency\n'
    assert result.candidate.text(ROOT+f'{other}/skip.md') == '# Target skip\n'
    assert len([op for op, _ in state['calls'] if op == 'translation']) == 2
    assert all('skip.md' not in d.get('path', '') for op, d in state['calls'] if op == 'critic')


def test_confirmed_url_repair_is_checked_and_published_exactly(system):
    state, run, commit, *_ = system
    commit({'ru/a.md': '# Article\n\n[Link](/ru/b.md)\n',
            'ru/b.md': '# B\n', 'en/b.md': '# B\n'})
    result = run()
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert result.candidate.text(ROOT+'en/a.md') == '# Article\n\n[Link](/en/b.md)\n'
    assert [op for op, _ in state['calls']] == ['translation', 'critic', 'repair', 'critic']
    assert len(result.quality.rounds) == 2
    assert result.quality.rounds[-1].candidate_sha == result.result_sha == result.checked_sha


@pytest.mark.parametrize('kind', ['save', 'report', 'candidate'])
def test_storage_and_report_errors_remain_visible_and_draft(system, kind):
    _, run, *_ = system
    observed = []
    def fail(value):
        raise RuntimeError('storage unavailable' if kind != 'report' else 'report unavailable')
    hooks = RunHooks(save=fail if kind == 'save' else None,
                     report=fail if kind == 'report' else observed.append,
                     candidate_progress=fail if kind == 'candidate' else None)
    result = run(hooks=hooks)
    assert result.status == 'RED' and result.errors
    assert result.publication.pr_number == 2
    assert result.publication.draft
    assert result.candidate.text(ROOT+'en/a.md') == '# Hello\n\nHello world.\n'
    if kind == 'save':
        assert observed[-1].status == 'RED'


def test_noop_translation_checks_without_empty_commit_or_pr(system):
    state, run, commit, _, _, remote_sha, *_ = system
    commit({'en/a.md': '# Hello\n\nHello world.\n'})
    result = run()
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert result.checked_sha == result.candidate_sha == state['sha']
    assert result.publication is None and not state['pulls']
    assert remote_sha('translation') is None
    assert [op for op, _ in state['calls']] == ['translation', 'critic']


def test_candidate_freeze_keeps_real_index_and_noop(system):
    from ydbdoc_review.links import Candidate
    from ydbdoc_review.publication import freeze
    state, _, _, repo, git, *_ = system
    original = Candidate.open(repo, state['sha'])
    staged = repo / 'unrelated.txt'
    staged.write_text('unrelated staged bytes')
    git('add', 'unrelated.txt')
    before = git('diff', '--cached', '--binary')
    final = freeze(original, {ROOT+'en/a.md': b'# Exact\r\n'})
    assert final.read(ROOT+'en/a.md') == b'# Exact\r\n'
    assert 'unrelated.txt' not in final.entries
    assert git('diff', '--cached', '--binary') == before
    assert freeze(final, {ROOT+'en/a.md': b'# Exact\r\n'}).sha == final.sha
    assert not (repo / ROOT / 'en/a.md').exists()


def test_fork_publishes_in_fork_with_original_base(system):
    state, run, _, _, git, _, publisher, _ = system
    state['source_repo'] = 'contributor/docs'
    publisher.repository = 'contributor/docs'
    publisher.remote_url = 'https://github.com/contributor/docs.git'
    git('config', '--add', f'url.{state["remote"]}.insteadOf',
        'https://x-access-token:dummy@github.com/contributor/docs.git')
    result = run()
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert result.publication.repository == 'contributor/docs'
    assert state['pulls'][0][0] == '/repos/contributor/docs/pulls'
    assert state['pulls'][0][1]['base'] == 'topic'


def test_existing_pr_exact_expected_head_and_noop_publication(system):
    from ydbdoc_review.publication import freeze
    state, run, _, _, git, remote_sha, publisher, _ = system
    first = run()
    assert first.status == 'GREEN', first.errors
    publisher.pr_number = first.publication.pr_number
    expected = publisher.preflight(first.snapshot)
    assert expected == first.candidate_sha
    unchanged = publisher.publish(first.candidate, first.snapshot, expected_head=expected,
                                  status='GREEN', checked_sha=first.checked_sha)
    assert unchanged.pushed_sha == expected and len(state['pulls']) == 1
    changed = freeze(first.candidate, {ROOT+'en/a.md': b'# Repair\n'})
    # Concurrent head moves to a third commit: old expected SHA cannot overwrite it.
    foreign = freeze(first.candidate, {'unrelated': b'foreign'})
    git('push', 'https://x-access-token:dummy@github.com/up/docs.git',
        foreign.sha+':refs/heads/translation')
    from ydbdoc_review.publication import PublicationError
    with pytest.raises(PublicationError, match=r'changed|conflict'):
        publisher.publish(changed, first.snapshot, expected_head=expected,
                          status='GREEN', checked_sha=changed.sha)
    assert remote_sha('translation') == foreign.sha
    assert len(state['pulls']) == 1


def test_cancellation_during_quality_keeps_last_repaired_candidate(system):
    state, run, *_ = system
    def handler(op, data):
        if op == 'translation':
            return 'Damaged'
        if op == 'critic':
            return GOOD
        return data['source']
    state['handler'] = handler
    candidates = []
    def on_candidate(candidate):
        candidates.append(candidate)
        if len(candidates) == 2:
            state['cancelled'] = True
    result = run(hooks=RunHooks(candidate_progress=on_candidate,
                               cancelled=lambda: state['cancelled']))
    assert result.status == 'RED' and result.cancelled
    assert result.candidate_sha == candidates[-1].sha
    assert result.candidate.text(ROOT+'en/a.md') == '# Hello\n\nHello world.\n'
    assert result.checked_sha != result.candidate_sha
    assert result.publication.draft and result.result_sha == result.candidate_sha


def test_cancel_after_first_chunk_retains_partial_text_and_raw_exchange(system):
    state, run, commit, *_ = system
    text = '\n\n'.join(f'Paragraph number {i} has meaningful words.' for i in range(80))
    commit({'ru/a.md': text})
    def stop_after_chunk(file):
        state['cancelled'] = True
    state['progress'] = stop_after_chunk
    result = run(budget=RequestBudget(2500, 500, lambda m: len(str(m))))
    assert result.cancelled and result.status == 'RED'
    assert result.candidate.text(ROOT+'en/a.md')
    assert result.candidate.text(ROOT+'en/a.md') != text
    assert ROOT+'en/a.md' in result.unfinished_files
    assert result.files[0].chunks[0].response
    assert len(result.attempts) == 1 and result.attempts[0].response_text
    assert result.publication.draft


def test_missing_asset_is_red_and_obtained_text_still_published(system):
    _, run, commit, *_ = system
    commit({'ru/a.md': '# Article\n\n![missing](absent.svg)\n'})
    result = run()
    assert result.status == 'RED' and result.publication.draft
    assert result.candidate.text(ROOT+'en/a.md') == '# Article\n\n![missing](absent.svg)\n'
    assert any('asset' in issue.problem.lower() for issue in result.issues)
    assert result.checked_sha == result.result_sha


def test_head_changed_after_push_is_not_reported_as_checked_remote(system):
    state, run, *_ = system
    def change_remote():
        subprocess.run(['git', '--git-dir', str(state['remote']), 'update-ref',
                        'refs/heads/translation', state['sha']], check=True, capture_output=True)
    state['published_read'] = change_remote
    result = run()
    assert result.status == 'RED' and any('current head' in e for e in result.errors)
    assert result.result_sha is None
    assert not result.publication.head_confirmed
    assert result.publication.pushed_sha == result.checked_sha
    assert state['pulls'][0][1]['draft']


def test_mechanical_protected_corruption_is_red_without_model(system):
    state, run, commit, *_ = system
    commit({'ru/a.md': None, 'ru/b.md': '# Hello `code`\n', 'en/a.md': '# Hello `wrong`\n'})
    state['changes'] = [dict(filename=ROOT+'ru/b.md', status='renamed',
                             previous_filename=ROOT+'ru/a.md', changes=0, additions=0, deletions=0)]
    result = run()
    assert result.status == 'RED' and result.publication.draft
    assert state['made'] == state['admitted'] == 0 and not state['calls']
    assert result.candidate.text(ROOT+'en/b.md') == '# Hello `wrong`\n'
    assert any(i.code == 'protected' for i in result.issues)


def test_edited_rename_failed_translation_keeps_mechanical_result_unfinished(system):
    state, run, commit, *_ = system
    commit({'ru/a.md': None, 'ru/b.md': '# Updated\n', 'en/a.md': '# Previous\n'})
    state['changes'] = [dict(filename=ROOT+'ru/b.md', status='renamed',
                             previous_filename=ROOT+'ru/a.md', changes=2, additions=1, deletions=1)]
    state['handler'] = lambda op, data: requests.exceptions.Timeout('provider unavailable')
    result = run()
    assert result.status == 'RED' and result.publication.draft
    assert result.candidate.read(ROOT+'en/a.md') is None
    assert result.candidate.text(ROOT+'en/b.md') == '# Previous\n'
    assert ROOT+'en/b.md' in result.unfinished_files
    assert len(state['calls']) == 1 and state['calls'][0][0] == 'translation'
    assert 'Previous' not in state['calls'][0][1]


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_language_translation_uses_fixed_source_and_keeps_other_pair(system, language):
    state, run, commit, *_ = system
    other = 'en' if language == 'ru' else 'ru'
    source = '# Привет\n\nМир.\n' if language == 'ru' else '# Hello\n\nWorld.\n'
    expected = '# Hello\n\nWorld.\n' if language == 'ru' else '# Привет\n\nМир.\n'
    commit({f'{language}/a.md': source, f'{other}/a.md': 'Old target must not seed translation',
            'en/untouched.md': '# Keep me\n', 'ru/untouched.md': '# Сохранить\n'})
    state['changes'] = [dict(filename=ROOT+f'{language}/a.md', status='modified')]
    def handler(op, data):
        if op == 'critic':
            return GOOD
        assert op == 'translation'
        assert 'Old target' not in data
        raw = data.split('\n\n', 1)[1]
        pairs = [('Привет', 'Hello'), ('Мир', 'World')]
        for ru, en in pairs:
            raw = raw.replace(ru, en) if language == 'ru' else raw.replace(en, ru)
        return raw
    state['handler'] = handler
    result = run()
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert result.candidate.text(ROOT+f'{other}/a.md') == expected
    assert result.candidate.text(ROOT+f'{language}/a.md') == source
    assert result.candidate.text(ROOT+'en/untouched.md') == '# Keep me\n'
    assert result.candidate.text(ROOT+'ru/untouched.md') == '# Сохранить\n'
    assert [op for op, _ in state['calls']] == ['translation', 'critic']


def test_publisher_never_labels_unchecked_candidate_green(system):
    state, run, _, _, _, _, publisher, _ = system
    checked = run()
    assert checked.status == 'GREEN'
    publisher.branch = 'another-translation'
    expected = publisher.preflight(checked.snapshot)
    published = publisher.publish(checked.candidate, checked.snapshot, expected_head=expected,
                                  status='GREEN', checked_sha='0'*40)
    assert published.draft
    assert state['pulls'][-1][1]['body'].startswith('RED — мержить нельзя')
    assert 'Итого:' in state['pulls'][-1][1]['body']
