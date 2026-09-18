"""Fresh acceptance assertions through CLI/Actions → HTTP/SDK/Git/actual YFM."""
import json
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.contract.test_t15_cli import process  # noqa: F401 -- infrastructure fixture only

ROOT = Path(__file__).resolve().parents[2]
MODES = ('doc_translate', 'doc_verify', 'doc_continue')


@pytest.fixture
def independent(process):  # noqa: F811 -- pytest fixture dependency
    p = process
    (p.repo / 'ydb/docs/index.md').write_text('# Docs\n')
    (p.repo / 'ydb/docs/toc.yaml').write_text('title: Docs\nitems:\n  - name: Home\n    href: index.md\n'
        '  - name: RU\n    href: ru/a.md\n  - name: EN\n    href: en/a.md\n')
    p.git('add', '.')
    p.git('commit', '-m', 'independent YFM input')
    p.git('push', str(p.remote), 'HEAD:refs/heads/topic')
    shim = p.root / 'tools/ydbdoc-review'
    shim.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable)
                    + ' -m tests.contract.t15_independent_boundary "$@"\n')
    shim.chmod(0o755)

    def invoke(mode, pr=1, action=False, **extra):
        env = p.env | dict(INPUT_MODE=mode, INPUT_REPO='up/docs', INPUT_PR=str(pr),
                           INPUT_CONFIG=str(p.root / 'models.json')) | extra
        env = {k: v for k, v in env.items() if v is not None}
        command = (['sh', str(ROOT / 'entrypoint.sh')] if action else
                   [sys.executable, '-m', 'tests.contract.t15_independent_boundary', mode,
                    '--repo', 'up/docs', '--pr', str(pr), '--config', str(p.root / 'models.json')])
        return subprocess.run(command, env=env, cwd=ROOT, text=True, capture_output=True, timeout=110)

    p.run = staticmethod(invoke)
    return p


def source_only(p):
    (p.repo / 'ydb/docs/en/a.md').unlink()
    p.git('add', '.')
    p.git('commit', '-m', 'independent source only')
    p.git('push', str(p.remote), 'HEAD:refs/heads/topic')


def successful(result):
    assert result.returncode == 0, result.stdout + result.stderr


def summaries(p, mode):
    return [r for r in p.rows() if r['entry_id'] == 'summary' and r['mode'] == mode]


@pytest.mark.parametrize('action', [False, True], ids=['CLI', 'Actions-entrypoint'])
@pytest.mark.parametrize('mode', MODES)
def test_complete_process_actual_yfm_http_sdk_git(independent, mode, action):
    p = independent
    if mode != 'doc_verify':
        source_only(p)
    if mode == 'doc_continue':
        successful(p.run('doc_translate'))
        p.update(model=[], comments=[], actual_builds=[])
    successful(p.run(mode, 2 if mode == 'doc_continue' else 1, action=action))
    state = p.read()
    assert state['actual_builds'] and all(b['status'] == 'success'
        and 'ydb/docs/en/a.md' in b['pages'] for b in state['actual_builds'])
    sha = state['actual_builds'][-1]['sha']
    branch = state['pulls']['2' if mode != 'doc_verify' else '1']['branch']
    actual = subprocess.check_output(['git', '--git-dir', str(p.remote), 'rev-parse', branch], text=True).strip()
    assert actual == sha
    assert summaries(p, mode)[-1]['status'] == 'GREEN'
    assert len(state['pulls']) == (1 if mode == 'doc_verify' else 2)
    assert len(state['comments']) == (1 if mode == 'doc_verify' else 2)
    assert any(sha in body for _, body in state['comments'])
    for _, body in state['comments']:
        assert all(label in body for label in ('Перевод:', 'Критик:', 'Исправления:', 'Итого:'))
    roles = [r for r, _ in state['model']]
    assert ('translation' in roles) == (mode == 'doc_translate')
    if mode == 'doc_translate':
        assert state['pulls']['2']['base'] == 'topic'


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('failure', ['acl', 'settings', 'runtime'])
def test_preflight_refusal_delivers_source_pr_comment(independent, mode, failure):
    p = independent
    extra = {}
    if failure == 'acl':
        extra['GITHUB_ACTOR'] = 'outsider'
    elif failure == 'settings':
        extra['YDBDOC_MAX_SOURCE_CHARACTERS'] = None
    else:
        (p.root / 'models.json').write_text('{}')
    result = p.run(mode, **extra)
    assert result.returncode == 1
    state = p.read()
    assert not state['model'] and not state.get('factories', 0)
    assert len(state['pulls']) == 1
    assert not (p.root / 'database.sqlite').exists()
    assert state['comments'], ('Required §6 source PR refusal absent', result.stdout)
    assert all(number == 1 for number, _ in state['comments'])
    assert all(method == 'POST' and path == '/repos/up/docs/issues/1/comments'
               for method, path in state['http'])


@pytest.mark.parametrize('mode', MODES)
def test_sdk_close_error_preserves_completed_outcome(independent, mode):
    p = independent
    if mode == 'doc_continue':
        source_only(p)
        successful(p.run('doc_translate'))
    p.update(close_error=True)
    result = p.run(mode, 2 if mode == 'doc_continue' else 1)
    assert summaries(p, mode)[-1]['status'] == 'GREEN'
    assert p.read()['comments']
    # A cleanup failure must not replace the completed result by a bare exception.
    assert 'GREEN:' in result.stdout or 'RED:' in result.stdout, result.stdout + result.stderr


def test_merged_translate_uses_current_main(independent):
    p = independent
    source_only(p)
    p.git('push', str(p.remote), 'HEAD:refs/heads/main')
    p.update(merged=True)
    successful(p.run('doc_translate'))
    assert p.read()['pulls']['2']['base'] == 'main'
    assert p.read()['actual_builds'][-1]['status'] == 'success'


@pytest.mark.parametrize('verify', [False, True])
def test_bilingual_skip_is_translate_only(independent, verify):
    p = independent
    p.update(changes=[dict(filename=f'ydb/docs/{lang}/a.md', status='modified') for lang in ('ru', 'en')])
    result = p.run('doc_verify' if verify else 'doc_translate')
    successful(result)
    if verify:
        assert [r for r, _ in p.read()['model']] == ['critic']
        assert p.read()['actual_builds']
    else:
        assert 'Перевод не требуется' in result.stdout
        assert not p.read()['model'] and not p.read().get('factories', 0)
    assert len(p.read()['pulls']) == 1 and p.read()['comments']


def test_http_files_error_not_no_work(independent):
    p = independent
    p.update(files_error=True)
    result = p.run('doc_translate')
    assert result.returncode == 1 and 'NO_WORK' not in result.stdout
    assert p.read()['comments'] and not p.read()['model']
    assert len(p.read()['pulls']) == 1


def test_paid_fallback_and_critic_persist_once(independent):
    p = independent
    source_only(p)
    path = p.root / 'models.json'
    config = json.loads(path.read_text())
    config['models']['translation']['alternative'] = (config['models']['translation']['main']
        | dict(base_url='https://fallback.model.invalid'))
    path.write_text(json.dumps(config))
    p.update(fallback_once=True)
    successful(p.run('doc_translate'))
    assert [r for r, _ in p.read()['model']] == ['translation', 'translation', 'critic']
    paid = [r for r in p.rows() if r['entry_id'] != 'summary']
    assert len(paid) == 3
    assert all(r['cost_rub'] == '0.25' for r in paid)
    assert p.read()['actual_builds'][-1]['status'] == 'success'


@pytest.mark.parametrize('missing', [True, False], ids=['missing', 'expired'])
def test_continue_context_refusal_before_model(independent, missing):
    p = independent
    source_only(p)
    successful(p.run('doc_translate'))
    with sqlite3.connect(p.root / 'database.sqlite') as db:
        if missing:
            db.execute('DELETE FROM run_objects')
        else:
            from datetime import UTC, datetime

            from ydbdoc_review.store import decode, encode
            manifests = db.execute("SELECT run_id, object_key, payload FROM run_objects WHERE generation = ''").fetchall()
            for run_id, key, payload in manifests:
                manifest = decode(payload)
                manifest['created_at'] = datetime(2000, 1, 1, tzinfo=UTC)
                db.execute("UPDATE run_objects SET payload = ? WHERE run_id = ? AND object_key = ? AND generation = ''",
                           (encode(manifest), run_id, key))
    p.update(model=[], factories=0, comments=[])
    result = p.run('doc_continue', 2)
    assert result.returncode == 1
    assert not p.read()['model'] and p.read()['factories'] == 0
    assert p.read()['comments']
    assert '14' in result.stdout


def test_storage_outage_is_not_context_expiry(independent):
    p = independent
    p.update(storage_error=True)
    result = p.run('doc_continue')
    assert result.returncode == 1
    assert 'storage unavailable' in result.stdout and '14' not in result.stdout
    assert p.read()['comments'] and not p.read()['model']


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('failure', ['model_error', 'cancel'])
def test_full_process_failure_reports_cost_and_partial_draft(independent, mode, failure):
    p = independent
    if mode != 'doc_verify':
        source_only(p)
    if mode == 'doc_continue':
        successful(p.run('doc_translate'))
    p.update(**{failure: True}, model=[], comments=[])
    if failure == 'cancel' and mode == 'doc_translate':
        p.update(cancel=False, cancel_after=2)
    result = p.run(mode, 2 if mode == 'doc_continue' else 1, action=True)
    assert result.returncode == 1
    assert summaries(p, mode)[-1]['status'] == 'RED'
    assert p.read()['comments']
    for _, body in p.read()['comments']:
        assert 'RED' in body and 'Итого:' in body
    if failure == 'cancel' and mode == 'doc_translate':
        assert p.read()['pulls']['2']['draft']
        branch = p.read()['pulls']['2']['branch']
        content = subprocess.check_output(['git', '--git-dir', str(p.remote),
            'show', branch + ':ydb/docs/en/a.md'], text=True)
        assert content == '# Hello\n\nHello world.\n'


@pytest.mark.parametrize('changed', ['source', 'result'])
def test_unrelated_commits_block_continue_before_factory(independent, changed):
    p = independent
    source_only(p)
    successful(p.run('doc_translate'))
    branch = 'topic' if changed == 'source' else p.read()['pulls']['2']['branch']
    p.git('fetch', str(p.remote), branch)
    p.git('checkout', '--detach', 'FETCH_HEAD')
    p.git('commit', '--allow-empty', '-m', 'independent unrelated change')
    p.git('push', str(p.remote), 'HEAD:refs/heads/' + branch)
    p.update(model=[], factories=0, comments=[])
    result = p.run('doc_continue', 2, action=True)
    assert result.returncode == 1
    assert 'После предыдущего запуска появились новые коммиты' in result.stdout
    assert not p.read()['model'] and p.read()['factories'] == 0
    assert p.read()['comments'] and len(p.read()['pulls']) == 2


def test_twenty_one_dependencies_reports_every_path_before_factory(independent):
    p = independent
    source_only(p)
    paths = [f'dep{i}.md' for i in range(21)]
    (p.repo / 'ydb/docs/ru/a.md').write_text('# Article\n\n' + '\n\n'.join(f'[D]({path})' for path in paths))
    for path in paths:
        (p.repo / 'ydb/docs/ru' / path).write_text('# Dependency\n')
    p.git('add', '.')
    p.git('commit', '-m', 'independent dependency overflow')
    p.git('push', str(p.remote), 'HEAD:refs/heads/topic')
    result = p.run('doc_translate', action=True)
    assert result.returncode == 1
    assert '21' in result.stdout and 'YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE' in result.stdout
    assert not p.read()['model'] and not p.read().get('factories', 0)
    assert len(p.read()['pulls']) == 1
    report = '\n'.join(body for _, body in p.read()['comments'])
    assert all(path in report for path in paths)


def test_only_three_continuations_across_processes(independent):
    p = independent
    source_only(p)
    successful(p.run('doc_translate'))
    for _ in range(3):
        successful(p.run('doc_continue', 2, action=True))
    p.update(model=[], factories=0, comments=[])
    result = p.run('doc_continue', 2, action=True)
    assert result.returncode == 1 and p.read()['comments']
    assert not p.read()['model'] and p.read()['factories'] == 0
    assert len(p.read()['pulls']) == 2


@pytest.mark.parametrize('mode', MODES)
def test_budget_admission_once_before_first_paid_call_and_next_run(independent, mode):
    from decimal import Decimal

    p = independent
    if mode != 'doc_verify':
        source_only(p)
    if mode == 'doc_continue':
        successful(p.run('doc_translate'))
    already = sum(Decimal(r['cost_rub']) for r in p.rows()
                  if r['entry_id'] != 'summary') if (p.root / 'database.sqlite').exists() else Decimal(0)
    p.update(model=[], daily_queries=0)
    limit = str(already + Decimal('.01'))
    successful(p.run(mode, 2 if mode == 'doc_continue' else 1, YDBDOC_DAILY_BUDGET_RUB=limit))
    assert p.read()['daily_queries'] == 1
    assert p.read()['model']
    p.update(model=[], factories=0, daily_queries=0)
    result = p.run('doc_verify', 1 if mode == 'doc_verify' else 2, YDBDOC_DAILY_BUDGET_RUB=limit)
    assert result.returncode == 1 and 'YDBDOC_DAILY_BUDGET_RUB' in result.stdout
    assert not p.read()['model'] and p.read()['factories'] == 0
    assert p.read()['daily_queries'] == 1


@pytest.mark.parametrize('mode', MODES)
def test_action_docker_shell_reaches_real_entrypoint(independent, mode):
    p = independent
    if mode != 'doc_verify':
        source_only(p)
    if mode == 'doc_continue':
        successful(p.run('doc_translate'))
    # Docker itself is unavailable: emulate only the executable/container boundary.
    # The actual action-docker.sh and entrypoint.sh both execute unmodified.
    docker = p.root / 'tools/docker'
    docker.write_text('#!' + sys.executable + '\n' + '''import json, os, subprocess, sys
from pathlib import Path
path = Path(os.environ['T15_FIXTURE']) / 'docker-calls.jsonl'
with path.open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'run':
    raise SystemExit(subprocess.call(['sh', os.environ['GITHUB_ACTION_PATH'] + '/entrypoint.sh']))
''')
    docker.chmod(0o755)
    env = p.env | dict(GITHUB_ACTION_PATH=str(ROOT), GITHUB_WORKSPACE=str(p.root),
        INPUT_MODE=mode, INPUT_REPO='up/docs', INPUT_PR='2' if mode == 'doc_continue' else '1',
        INPUT_CONFIG=str(p.root / 'models.json'))
    result = subprocess.run(['bash', str(ROOT / 'action-docker.sh')], env=env, cwd=ROOT,
                            text=True, capture_output=True, timeout=110)
    successful(result)
    calls = [json.loads(line) for line in (p.root / 'docker-calls.jsonl').read_text().splitlines()]
    assert [args[0] for args in calls] == ['build', 'run', 'rmi']
    forwarded = [calls[1][i + 1] for i, arg in enumerate(calls[1][:-1]) if arg == '-e']
    assert 'MODEL_TOKEN' in forwarded
    assert all(name in forwarded for name in ('YDBDOC_ALLOWED_ACTORS', 'YDBDOC_DAILY_BUDGET_RUB',
        'YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE', 'YDBDOC_MAX_SOURCE_CHARACTERS'))
    assert 'dummy' not in calls[1]
    assert p.read()['actual_builds'][-1]['status'] == 'success'


def test_continue_repairs_selected_only_and_preserves_other_bytes(independent):
    p = independent
    source_only(p)
    untouched = '# Good\n\nUNSELECTED_SENTINEL.\n'
    (p.repo / 'ydb/docs/ru/good.md').write_text(untouched)
    toc = p.repo / 'ydb/docs/toc.yaml'
    toc.write_text(toc.read_text() + '  - name: Good RU\n    href: ru/good.md\n'
                   '  - name: Good EN\n    href: en/good.md\n')
    p.git('add', '.')
    p.git('commit', '-m', 'second independent source')
    p.git('push', str(p.remote), 'HEAD:refs/heads/topic')
    p.update(changes=[dict(filename='ydb/docs/ru/' + name, status='added')
                      for name in ('a.md', 'good.md')])
    successful(p.run('doc_translate'))
    p.update(model=[], comments=[], actual_builds=[])
    successful(p.run('doc_continue', 2))
    prompts = json.dumps(p.read()['model'])
    assert 'UNSELECTED_SENTINEL' not in prompts
    assert [role for role, _ in p.read()['model']] == ['critic', 'repair', 'critic']
    branch = p.read()['pulls']['2']['branch']
    raw = subprocess.check_output(['git', '--git-dir', str(p.remote), 'show',
                                   branch + ':ydb/docs/en/good.md'])
    assert raw == untouched.encode()
    assert 'ydb/docs/en/good.md' in p.read()['actual_builds'][-1]['pages']


@pytest.mark.parametrize('mode', MODES)
def test_red_common_loop_reports_real_published_coordinates(independent, mode):
    p = independent
    if mode != 'doc_verify':
        source_only(p)
    if mode == 'doc_continue':
        successful(p.run('doc_translate'))
    p.update(critic_issue=True, model=[], comments=[], actual_builds=[])
    result = p.run(mode, 2 if mode == 'doc_continue' else 1)
    assert result.returncode == 1
    roles = [role for role, _ in p.read()['model']]
    assert roles == (['translation'] if mode == 'doc_translate' else []) + [
        'critic', 'repair', 'critic', 'repair', 'critic']
    number = '1' if mode == 'doc_verify' else '2'
    branch = p.read()['pulls'][number]['branch']
    sha = subprocess.check_output(['git', '--git-dir', str(p.remote), 'rev-parse', branch], text=True).strip()
    assert p.read()['pulls'][number]['draft']
    detail = next(body for n, body in p.read()['comments'] if n == int(number))
    assert f'/blob/{sha}/ydb/docs/en/a.md#L1' in detail
    assert '# Hello' in detail and 'Correct the heading' in detail
    assert summaries(p, mode)[-1]['status'] == 'RED'
    assert p.read()['actual_builds'][-1]['sha'] == sha
