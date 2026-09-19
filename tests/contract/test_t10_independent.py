"""Independent T10 acceptance. Fixture HTTP/local Git plumbing adapted from T10.
Assertions and adversarial scenarios below are independently specified.
Real runner/document/quality/build/publication; no live services.
"""
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
import yaml

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
def rig(git_repo, tmp_path, monkeypatch):
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
    state = dict(merged=False, source_repo='up/docs', changes=[], pulls=[], calls=[], records=[], attempts=[], http=[],
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
        state['http'].append((req.method, req.url))
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
        return ModelClient(record_request=state['records'].append, record_attempt=state['attempts'].append,
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


@pytest.mark.parametrize('reason', ['acl', 'bilingual', 'empty', 'files_api', 'missing_source',
                                    'budget', 'fork', 'clone', 'branch_exists'])
def test_early_exit_has_no_model_git_publication_and_final_hooks(rig, reason):
    s, run, _, _, git, remote_sha, publisher, _ = rig
    kw = {}
    if reason == 'acl':
        kw['actor'] = 'unauthorized'
    elif reason == 'bilingual':
        s['changes'].append(dict(filename=ROOT+'en/a.md', status='added'))
    elif reason == 'empty':
        s['changes'] = []
    elif reason == 'files_api':
        s['fail_files'] = True
    elif reason == 'missing_source':
        s['changes'] = [dict(filename=ROOT+'ru/gone.md', status='modified')]
    elif reason == 'budget':
        def denied():
            raise RuntimeError('YDBDOC_DAILY_BUDGET_RUB exhausted')
        kw['admit'] = denied
    elif reason == 'fork':
        s['source_repo'] = 'fork/docs'
    elif reason == 'clone':
        publisher.remote_url = 'https://github.com/wrong/docs.git'
    elif reason == 'branch_exists':
        git('push', 'https://x-access-token:dummy@github.com/up/docs.git',
            s['sha']+':refs/heads/translation')
    before = remote_sha('translation')
    result = run(**kw)
    assert result.status == ('NO_WORK' if reason in {'empty', 'bilingual'} else 'RED')
    assert result.candidate is None and result.publication is None
    assert not s['calls'] and not s['made'] and not s['pulls']
    assert remote_sha('translation') == before
    assert s['saved'] == s['reported'] == [result]
    if reason == 'acl':
        assert not s['http']
    if reason == 'missing_source':
        assert 'ru/gone.md' in result.message


@pytest.mark.parametrize('kind', ['dependencies', 'raw_characters'])
def test_limits_report_full_volume_without_truncating_or_model(rig, kind):
    s, run, commit, _, _, remote_sha, _, settings = rig
    sources = {'ru/a.md': '# A\n\n[B](b.md)\n', 'ru/b.md': '# B\n\n[C](c.md)\n',
               'ru/c.md': '# C\n\n[D](d.md)\n', 'ru/d.md': '# D\n'}
    commit(sources)
    total = sum(map(len, sources.values()))
    if kind == 'dependencies':
        settings = replace(settings, max_dependency_files=1)
    else:
        settings = replace(settings, max_source_characters=total-1)
    result = run(settings=settings)
    assert result.status == 'RED' and result.candidate is None
    assert not s['made'] and not s['admitted'] and not s['pulls']
    assert remote_sha('translation') is None
    if kind == 'dependencies':
        assert 'YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE' in result.message
        assert '3' in result.message
        for path in ('ru/b.md', 'ru/c.md', 'ru/d.md'):
            assert path in result.message
    else:
        assert 'YDBDOC_MAX_SOURCE_CHARACTERS' in result.message
        assert str(total) in result.message and str(total-1) in result.message


@pytest.mark.parametrize('operation', ['add', 'edit', 'delete', 'rename', 'rename_edit'])
def test_mirror_bytes_publication_base_and_mechanical_zero_calls(rig, operation):
    s, run, commit, _, git, remote_sha, _, _ = rig
    if operation != 'add':
        commit({'en/a.md': '# Previous\n'})
    if operation in {'rename', 'rename_edit'}:
        commit({'ru/a.md': None, 'ru/next.md': '# Next\n'})
        s['changes'] = [dict(filename=ROOT+'ru/next.md', previous_filename=ROOT+'ru/a.md',
                             status='renamed', changes=0 if operation == 'rename' else 2,
                             additions=0 if operation == 'rename' else 1,
                             deletions=0 if operation == 'rename' else 1)]
    elif operation == 'delete':
        commit({'ru/a.md': None})
        s['changes'] = [dict(filename=ROOT+'ru/a.md', status='removed')]
    elif operation == 'edit':
        s['changes'][0]['status'] = 'modified'
    initial = s['sha']
    result = run()
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert result.result_sha == result.candidate_sha == result.checked_sha == remote_sha('translation')
    assert git('rev-parse', result.candidate_sha+'^').decode().strip() == initial
    assert result.snapshot.source_sha == initial
    assert result.publication.base == 'topic' and not result.publication.draft
    if operation in {'delete', 'rename', 'rename_edit'}:
        assert result.candidate.read(ROOT+'en/a.md') is None
    if operation.startswith('rename'):
        assert result.candidate.text(ROOT+'en/next.md') == (
            '# Previous\n' if operation == 'rename' else '# Next\n')
    elif operation != 'delete':
        assert result.candidate.text(ROOT+'en/a.md') == '# Hello\n\nHello world.\n'
    if operation in {'delete', 'rename'}:
        assert not s['made'] and not s['admitted'] and not s['calls'] and not result.attempts
    else:
        assert s['made'] == s['admitted'] == 1
        assert 'Previous' not in s['calls'][0][1]


@pytest.mark.parametrize('merged', [False, True])
def test_snapshot_ignores_dirty_tree_and_later_source_head(rig, merged):
    s, run, commit, repo, git, remote_sha, _, _ = rig
    old = s['sha']
    s['merged'] = merged
    fixed = commit({'ru/a.md': '# Frozen\n'})
    if merged:
        s.update(sha=old, base_sha=fixed)
    before = git('status', '--porcelain')
    def moved(file):
        (repo / ROOT / 'ru/a.md').write_text('# Dirty\n')
        s['sha'] = old
    s['progress'] = moved
    result = run()
    assert result.status == 'GREEN', result.errors
    assert result.snapshot.source_sha == fixed
    assert result.candidate.text(ROOT+'en/a.md') == '# Frozen\n'
    assert result.candidate.text(ROOT+'ru/a.md') == '# Frozen\n'
    assert result.result_sha == remote_sha('translation') == result.checked_sha
    assert result.publication.base == ('main' if merged else 'topic')
    assert before == b'' and (repo / ROOT / 'ru/a.md').read_text() == '# Dirty\n'


@pytest.mark.parametrize('failure', ['structural', 'file_error', 'between_files',
                                    'inside_chunk', 'after_chunk'])
def test_red_and_interruption_keep_exact_available_text_assets_and_context(rig, failure):
    s, run, commit, _, _, remote_sha, _, _ = rig
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    source = '# First\n\n![Diagram](pic.svg)\n'
    if failure in {'inside_chunk', 'after_chunk'}:
        source += '\n\n'.join(f'Paragraph {i} has useful information.' for i in range(90))
    commit({'ru/a.md': source, 'ru/pic.svg': svg, 'ru/b.md': '# Second\n'})
    s['changes'].append(dict(filename=ROOT+'ru/b.md', status='added'))
    translations = []
    def response(op, data):
        if op == 'translation':
            translations.append(data)
            if failure == 'inside_chunk' and len(translations) == 2:
                return KeyboardInterrupt('interrupted in HTTP of next chunk')
            if '/en/b.md' in data and failure == 'file_error':
                return requests.exceptions.Timeout('second article unavailable')
            raw = data.split('\n\n', 1)[1]
            return raw + '\n# Unexpected section\n' if failure == 'structural' else raw
        if op == 'critic':
            return GOOD
        return data['source'] + '\n# Unexpected section\n'
    s['handler'] = response
    snapshots = []
    def progress(file):
        snapshots.append(file)
        if failure == 'between_files' and not file.unfinished:
            s['cancelled'] = True
        elif failure == 'after_chunk':
            s['cancelled'] = True
    s['progress'] = progress
    opts = dict(budget=RequestBudget(2600, 500, lambda m: len(str(m)))) if (
        failure in {'inside_chunk', 'after_chunk'}) else {}
    result = run(**opts)
    assert result.status == 'RED' and result.publication.draft
    assert result.result_sha == result.candidate_sha == remote_sha('translation')
    assert result.candidate.read(ROOT+'en/pic.svg') == svg
    assert result.candidate.text(ROOT+'en/a.md')
    assert s['saved'] == s['reported'] == [result]
    assert result.attempts == tuple(s['attempts'])
    assert result.files[0].chunks[0].response
    if failure in {'inside_chunk', 'after_chunk'}:
        assert result.cancelled and result.checked_sha is None
        assert set(result.unfinished_files) == {ROOT+'en/a.md', ROOT+'en/b.md'}
        assert result.candidate.text(ROOT+'en/a.md') == snapshots[-1].text != source
        assert len(translations) == (2 if failure == 'inside_chunk' else 1)
    elif failure in {'between_files', 'file_error'}:
        assert result.unfinished_files == (ROOT+'en/b.md',)
        assert result.candidate.text(ROOT+'en/a.md') == source
    else:
        assert len(result.quality.rounds) == 1  # §5.1: repair returned unchanged text
        assert len([x for x in s['calls'] if x[0] == 'repair']) <= 4
        assert result.checked_sha == result.candidate_sha


@pytest.mark.parametrize('failure', ['invalid_toc', 'interrupt_build', 'interrupt_after_repair'])
def test_failed_or_interrupted_build_never_checks_later_candidate(rig, failure):
    from ydbdoc_review.build import build_candidate
    s, run, commit, _, _, remote_sha, _, _ = rig
    if failure == 'invalid_toc':
        commit({'toc.yaml': 'items: [unterminated\n'})
    candidates = []
    builds = []
    def build(candidate):
        builds.append(candidate.sha)
        if failure == 'interrupt_build' or (failure == 'interrupt_after_repair' and len(builds) == 2):
            raise KeyboardInterrupt('interrupted at builder boundary')
        return build_candidate(candidate)
    if failure == 'interrupt_after_repair':
        s['handler'] = lambda op, data: ('Broken' if op == 'translation' else
                                       GOOD if op == 'critic' else data['source'])
    result = run(build=build, hooks=RunHooks(candidate_progress=candidates.append,
                                            save=s['saved'].append, report=s['reported'].append))
    assert result.status == 'RED' and result.publication.draft
    assert result.result_sha == remote_sha('translation') == candidates[-1].sha
    assert result.candidate.text(ROOT+'en/a.md') == '# Hello\n\nHello world.\n'
    if failure == 'invalid_toc':
        assert all(not round_.build.ok_for(result.candidate_sha) for round_ in result.quality.rounds)
        assert any(i.code == 'build' for i in result.issues)
    else:
        assert result.cancelled
        assert result.checked_sha != result.result_sha
    if failure == 'interrupt_after_repair':
        assert len(candidates) == 2 and candidates[0].sha != candidates[1].sha
    assert s['saved'] == s['reported'] == [result]


@pytest.mark.parametrize('failure', ['push', 'create', 'before_push_race', 'after_push_race'])
def test_remote_errors_never_claim_success_and_preserve_local_candidate(rig, failure):
    s, run, _, _, git, remote_sha, _, _ = rig
    if failure == 'push':
        hook = s['remote'] / 'hooks/pre-receive'
        hook.parent.mkdir(exist_ok=True)
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
    elif failure == 'create':
        s['fail_create'] = True
    elif failure == 'before_push_race':
        s['progress'] = lambda file: git('push', 'https://x-access-token:dummy@github.com/up/docs.git',
                                       s['sha']+':refs/heads/translation')
    else:
        def race():
            subprocess.run(['git', '--git-dir', str(s['remote']), 'update-ref',
                            'refs/heads/translation', s['sha']], check=True, capture_output=True)
        s['published_read'] = race
    result = run()
    assert result.status == 'RED' and result.errors
    assert result.result_sha is None
    assert result.candidate.text(ROOT+'en/a.md') == '# Hello\n\nHello world.\n'
    assert s['saved'] == s['reported'] == [result]
    if failure in {'before_push_race', 'after_push_race'}:
        assert remote_sha('translation') == s['sha'] != result.candidate_sha
    if failure == 'create':
        assert result.publication.pushed_sha == result.candidate_sha == remote_sha('translation')
        assert result.publication.pr_number is None


@pytest.mark.parametrize('hook', ['save', 'report', 'file_progress', 'candidate_progress'])
def test_callback_errors_keep_context_and_force_draft(rig, hook):
    s, run, _, _, _, _, _, _ = rig
    seen = []
    def fail(value):
        seen.append(value)
        raise RuntimeError('independent callback outage')
    opts = dict(save=s['saved'].append, report=s['reported'].append)
    opts[hook] = fail
    result = run(hooks=RunHooks(**opts))
    assert result.status == 'RED' and result.errors and seen
    assert result.publication.draft and result.result_sha == result.candidate_sha
    assert result.files[0].text == '# Hello\n\nHello world.\n'
    assert result.attempts == tuple(s['attempts'])
    if hook == 'save':
        assert s['reported'][0].status == 'RED'


def test_model_close_failure_also_leaves_red_pr_draft(rig, monkeypatch):
    s, run, *_ = rig
    original_close = ModelClient.close
    def close(client):
        original_close(client)
        raise RuntimeError('transport cleanup failed')
    monkeypatch.setattr(ModelClient, 'close', close)
    result = run()
    assert result.status == 'RED' and any('close' in e for e in result.errors)
    assert result.publication.draft, 'RED finalization must also draft after model close failure'
    assert s['pulls'][-1][1]['draft']


def test_attempts_costs_and_context_use_final_repaired_sha(rig):
    s, run, _, _, _, _, _, _ = rig
    s['handler'] = lambda op, data: ('Broken' if op == 'translation' else
                                   GOOD if op == 'critic' else data['source'])
    result = run(instruction='Preserve technical detail', glossary=(('Hello', 'Hello'),))
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert result.files[0].text == 'Broken'
    assert result.candidate.text(ROOT+'en/a.md') == '# Hello\n\nHello world.\n'
    assert result.checked_sha == result.result_sha == result.quality.rounds[-1].candidate_sha
    assert [a.request.operation for a in result.attempts] == ['translation', 'critic', 'repair', 'critic']
    assert result.attempts == tuple(s['attempts'])
    assert all(a.response_text for a in result.attempts)
    assert result.cost_breakdown == dict(translation=Decimal('.25'), critic=Decimal('.50'),
                                         repair=Decimal('.25'), total=Decimal('1'))
    assert result.selected_files[0].instruction == 'Preserve technical detail'
    assert result.selected_files[0].source == '# Hello\n\nHello world.\n'
    assert s['saved'] == s['reported'] == [result]


def test_publisher_rejects_green_label_for_subsequent_unchecked_sha(rig):
    from ydbdoc_review.publication import freeze
    s, run, _, _, _, remote_sha, publisher, _ = rig
    first = run()
    assert first.status == 'GREEN'
    unchecked = freeze(first.candidate, {ROOT+'en/a.md': b'# Changed after validation\n'})
    publisher.branch = 'later'
    expected = publisher.preflight(first.snapshot)
    published = publisher.publish(unchecked, first.snapshot, expected_head=expected,
                                  status='GREEN', checked_sha=first.checked_sha)
    assert published.draft and published.pushed_sha == remote_sha('later') == unchecked.sha
    assert s['pulls'][-1][1]['body'].startswith('RED\n')


def test_fork_with_explicit_matching_repository_publishes_to_source_branch(rig):
    s, run, _, _, git, _, publisher, _ = rig
    s['source_repo'] = 'fork/docs'
    publisher.repository = 'fork/docs'
    publisher.remote_url = 'https://github.com/fork/docs.git'
    git('config', '--add', f'url.{s["remote"]}.insteadOf',
        'https://x-access-token:dummy@github.com/fork/docs.git')
    result = run()
    assert result.status == 'GREEN', result.errors
    assert result.publication.repository == 'fork/docs' and result.publication.base == 'topic'
    assert s['pulls'][0][0] == '/repos/fork/docs/pulls'


def test_error_in_first_file_still_translates_and_publishes_second(rig):
    s, run, commit, _, _, remote_sha, _, _ = rig
    commit({'ru/b.md': '# Second\n'})
    s['changes'].append(dict(filename=ROOT+'ru/b.md', status='added'))
    def handler(op, data):
        if op == 'translation':
            return requests.exceptions.Timeout('first failed') if '/en/a.md' in data else (
                data.split('\n\n', 1)[1])
        return GOOD if op == 'critic' else data['source']
    s['handler'] = handler
    result = run()
    assert result.status == 'RED' and result.publication.draft
    assert result.candidate.read(ROOT+'en/a.md') is None
    assert result.candidate.text(ROOT+'en/b.md') == '# Second\n'
    assert result.unfinished_files == (ROOT+'en/a.md',)
    assert result.result_sha == remote_sha('translation')
    assert len(result.attempts) == len(s['attempts']) == 3
    assert result.attempts[0].error


def test_existing_publication_uses_original_expected_head_after_concurrent_commit(rig):
    from ydbdoc_review.publication import PublicationError, freeze
    s, run, _, _, git, remote_sha, publisher, _ = rig
    first = run()
    publisher.pr_number = first.publication.pr_number
    expected = publisher.preflight(first.snapshot)
    assert expected == first.result_sha
    foreign = freeze(first.candidate, {'foreign.txt': b'keep concurrent edit'})
    proposed = freeze(first.candidate, {ROOT+'en/a.md': b'# Later translation\n'})
    git('push', 'https://x-access-token:dummy@github.com/up/docs.git',
        foreign.sha+':refs/heads/translation')
    with pytest.raises(PublicationError):
        publisher.publish(proposed, first.snapshot, expected_head=expected,
                          status='RED', checked_sha=first.checked_sha)
    assert remote_sha('translation') == foreign.sha
    assert len(s['pulls']) == 1


@pytest.mark.parametrize('location', ['body', 'description'])
def test_actual_runner_preserves_escaped_url_destination_through_repair(rig, location):
    from ydbdoc_review.links import references
    _, run, commit, *_ = rig
    link = '[Read](/ru/topic\\).md)'
    source = '# Article\n\n'+link+'\n' if location == 'body' else (
        "---\ndescription: '"+link+"'\n---\n# Article\n")
    commit({'ru/a.md': source, 'ru/topic).md': '# Topic\n', 'en/topic).md': '# Topic\n'})
    result = run()
    target = result.candidate.text(ROOT+'en/a.md')
    content = yaml.safe_load(target.split('---', 2)[1])['description'] if location == 'description' else target
    destinations = [unquote(ref.href) for ref in references(content)]
    assert '/en/topic).md' in destinations, (target, destinations, result.status,
                                              result.publication, result.issues)
    assert '/en/topic' not in destinations
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert result.checked_sha == result.result_sha


def test_whole_tree_unselected_english_russian_link_cannot_publish_green(rig):
    _, run, commit, *_ = rig
    commit({'en/unselected.md': '# Existing\n\n[Russian](/ru/extra.md-extra)\n',
            'ru/extra.md-extra': '# Russian target\n'})
    result = run()
    assert result.status == 'RED', 'Whole final tree has EN -> RU link with no EN target'
    assert result.publication.draft
    assert any('extra.md-extra' in i.problem for i in result.issues)


@pytest.mark.parametrize('callback', ['save', 'report'])
def test_interrupt_during_final_hook_returns_cancelled_result_and_draft(rig, callback):
    s, run, _, _, _, remote_sha, _, _ = rig
    def interrupt(result):
        assert result.result_sha == remote_sha('translation')
        raise KeyboardInterrupt('interruption during final '+callback)
    opts = dict(save=s['saved'].append, report=s['reported'].append)
    opts[callback] = interrupt
    try:
        result = run(hooks=RunHooks(**opts))
    except KeyboardInterrupt:
        pytest.fail('Final callback interrupt escaped run_translate; ready PR was not finalized RED')
    assert result.status == 'RED' and result.cancelled
    assert result.publication.draft
    assert result.result_sha == remote_sha('translation')
