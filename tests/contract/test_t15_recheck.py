"""Fresh T15 recheck: real CLI with faults only at HTTP/SDK boundaries."""
import json
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.contract.test_t15_cli import assert_saved, process  # noqa: F401
from tests.contract.test_t15_independent import (  # noqa: F401
    MODES,
    independent,
    source_only,
    successful,
)


def boundary_main():
    """Extend existing isolated transport, retaining actual YFM and product code."""
    import requests
    import ydb

    from tests.contract import t15_independent_boundary as boundary

    original_run = runpy.run_module

    def launch(*args, **kwargs):
        path = Path(os.environ['T15_FIXTURE']) / 'http.json'

        def read():
            return json.loads(path.read_text())

        def write(state):
            path.write_text(json.dumps(state))

        original_git_run = subprocess.run

        def git_run(command, *a, **kw):
            if isinstance(command, list) and command[0] == 'git' and 'push' in command:
                state = read()
                state['git_pushes'] = state.get('git_pushes', 0) + 1
                write(state)
            return original_git_run(command, *a, **kw)

        subprocess.run = git_run
        original_send = requests.Session.send

        def send(session, request, **kw):
            state = read()
            if state.get('credential_status') and 'api.github.com' in request.url:
                state['http'].append([request.method, urlsplit(request.url).path])
                write(state)
                response = requests.Response()
                response.status_code = state['credential_status']
                response._content = ('Credential rejected ' + os.environ['GITHUB_TOKEN']
                                     + ' ' + os.environ['GITHUB_PUSH_TOKEN']).encode()
                return response
            if state.get('interrupt_critic') and '/critic/' in request.url:
                state['interrupt_critic'] = False
                write(state)
                raise KeyboardInterrupt('interrupted at critic HTTP boundary')
            return original_send(session, request, **kw)

        requests.Session.send = send
        original_pool, original_driver = ydb.SessionPool, ydb.Driver

        def observe(resource, name):
            original_stop = resource.stop

            def stop():
                state = read()
                state.setdefault('stops', []).append(name)
                write(state)
                original_stop()
                if name == 'pool' and state.get('stop_fault'):
                    error = KeyboardInterrupt if state['stop_fault'] == 'interrupt' else RuntimeError
                    raise error('pool cleanup ' + os.environ['GITHUB_TOKEN']
                                + ' ' + os.environ['MODEL_TOKEN'])

            resource.stop = stop
            return resource

        ydb.Driver = lambda **kw: observe(original_driver(**kw), 'driver')
        ydb.SessionPool = lambda driver: observe(original_pool(driver), 'pool')
        return original_run(*args, **kwargs)

    boundary._original_run = launch
    runpy.run_module = boundary.run_product
    boundary.cli_boundary.main()


def invoke(p, mode, pr=1, **extra):
    env = {k: v for k, v in (p.env | extra).items() if v is not None}
    return subprocess.run([sys.executable, '-m', 'tests.contract.test_t15_recheck', mode,
        '--repo', 'up/docs', '--pr', str(pr), '--config', str(p.root / 'models.json')],
        cwd=Path(__file__).resolve().parents[2], env=env, text=True,
        capture_output=True, timeout=110)


def no_work_effects(p):
    state = p.read()
    assert not state['model'] and not state.get('factories', 0)
    assert len(state['pulls']) == 1
    assert not (p.root / 'database.sqlite').exists()
    assert p.git('status', '--porcelain') == b''


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('failure', ['acl', 'settings', 'runtime'])
@pytest.mark.parametrize('report_error', [False, True], ids=['delivered', 'report-error'])
def test_early_refusal_has_reason_and_only_source_comment(independent, mode, failure, report_error):  # noqa: F811
    p = independent
    before = p.git('rev-parse', 'HEAD')
    extra = {}
    if failure == 'acl':
        extra['GITHUB_ACTOR'] = 'untrusted'
        reason = 'YDBDOC_ALLOWED_ACTORS'
    elif failure == 'settings':
        extra['YDBDOC_MAX_SOURCE_CHARACTERS'] = 'secret-invalid-number'
        reason = 'YDBDOC_MAX_SOURCE_CHARACTERS'
    else:
        (p.root / 'models.json').write_text('{"models": "secret-invalid-runtime"}')
        reason = 'technical model configuration'
    p.update(report_error=report_error)
    result = invoke(p, mode, **extra)
    assert result.returncode == 1 and result.stdout.startswith('RED:')
    assert reason in result.stdout
    assert 'secret-invalid' not in result.stdout + result.stderr
    assert ('report:' in result.stdout) == report_error
    no_work_effects(p)
    assert p.git('rev-parse', 'HEAD') == before
    assert p.read()['http'] == [['POST', '/repos/up/docs/issues/1/comments']]
    comments = p.read()['comments']
    assert len(comments) == (0 if report_error else 1)
    if comments:
        number, body = comments[0]
        assert number == 1 and 'RED' in body and reason in body
        assert 'secret-invalid' not in body


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('token', [None, ''], ids=['absent', 'empty'])
def test_missing_github_credential_is_local_honest_refusal(independent, mode, token):  # noqa: F811
    p = independent
    result = invoke(p, mode, GITHUB_TOKEN=token)
    assert result.returncode == 1
    assert 'GITHUB_TOKEN is required' in result.stdout
    assert not p.read()['http'] and not p.read()['comments']
    no_work_effects(p)


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('status', [401, 403])
def test_rejected_credentials_do_not_leak_or_claim_report_success(independent, mode, status):  # noqa: F811
    p = independent
    p.update(credential_status=status)
    result = invoke(p, mode, GITHUB_ACTOR='untrusted', GITHUB_TOKEN='private-github-sentinel',
                    GITHUB_PUSH_TOKEN='private-push-sentinel')
    assert result.returncode == 1 and result.stdout.startswith('RED:')
    assert 'YDBDOC_ALLOWED_ACTORS' in result.stdout and f'HTTP {status}' in result.stdout
    assert 'report:' in result.stdout and not p.read()['comments']
    assert 'private-' not in result.stdout + result.stderr
    assert p.read()['http'] == [['POST', '/repos/up/docs/issues/1/comments']]
    no_work_effects(p)


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('fault', ['exception', 'interrupt'])
def test_cleanup_fault_retains_completed_result_and_stops_driver_once(independent, mode, fault):  # noqa: F811
    p = independent
    if mode != 'doc_verify':
        source_only(p)
    if mode == 'doc_continue':
        successful(p.run('doc_translate'))
    p.update(stop_fault=fault, stops=[], model=[], comments=[], http=[])
    result = invoke(p, mode, 2 if mode == 'doc_continue' else 1)
    successful(result)
    assert result.stdout.startswith('GREEN:')
    assert 'Cleanup warning' in result.stderr and 'pool cleanup' in result.stderr
    assert 'dummy' not in result.stdout + result.stderr
    state = p.read()
    assert state['stops'] == ['pool', 'driver']
    context, attempts = assert_saved(p, mode, 'GREEN')
    assert not context['result']['cancelled']  # work completed before cleanup interrupt
    expected = {'doc_translate': ['translation', 'critic'], 'doc_verify': ['critic'],
                'doc_continue': ['critic', 'repair', 'critic']}[mode]
    assert [role for role, _ in state['model']] == expected
    assert len(attempts) == len(expected)
    assert len({row['entry_id'] for row in attempts}) == len(attempts)
    assert len(state['comments']) == (1 if mode == 'doc_verify' else 2)
    assert all('GREEN' in body for _, body in state['comments'])
    assert state['http'].count(['POST', '/repos/up/docs/pulls']) == int(mode == 'doc_translate')
    assert state.get('git_pushes', 0) == 1  # includes an unchanged-SHA verify push
    assert state['actual_builds'][-1]['sha'] == context['result']['checked_sha']


def test_keyboard_interrupt_during_work_preserves_partial_red(independent):  # noqa: F811
    p = independent
    source_only(p)
    p.update(interrupt_critic=True, stop_fault='interrupt')
    result = invoke(p, 'doc_translate')
    assert result.returncode == 1 and result.stdout.startswith('RED:')
    context, attempts = assert_saved(p, 'doc_translate', 'RED')
    assert context['result']['cancelled']
    assert context['final_files']['ydb/docs/en/a.md'] == b'# Hello\n\nHello world.\n'
    assert p.read()['pulls']['2']['draft']
    assert p.read()['stops'] == ['pool', 'driver']
    assert [role for role, _ in p.read()['model']] == ['translation']
    assert len(attempts) == 2  # completed translation plus interrupted pending critic
    assert len(p.read()['comments']) == 2
    assert all('RED' in body for _, body in p.read()['comments'])


@pytest.mark.parametrize('mode', MODES)
def test_examples_load_readme_runtime_and_exact_product_variables(tmp_path, monkeypatch, mode):
    import yaml

    from ydbdoc_review.config.loader import load_settings, require_actor
    from ydbdoc_review.config.runtime import load_runtime

    root = Path(__file__).resolve().parents[2]
    data = yaml.safe_load((root / f'examples/ydb-github-doc-{mode.removeprefix("doc_")}-on-label.yml').read_text())
    job = data['jobs']['document']
    step = job['steps'][-1]
    assert step['with']['mode'] == mode and mode in job['if']
    expected = {'YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE': '20',
                'YDBDOC_MAX_SOURCE_CHARACTERS': '250000',
                'YDBDOC_ALLOWED_ACTORS': 'writer', 'YDBDOC_DAILY_BUDGET_RUB': '100'}
    variables = {name for name, value in step['env'].items() if 'vars.' in value}
    assert variables == expected.keys()
    for name, value in expected.items():
        assert step['env'][name] == '${{ vars.' + name + ' }}'
        monkeypatch.setenv(name, value)
    monkeypatch.setenv('YDB_SA_KEY', '{}')
    require_actor(load_settings(), 'writer')
    raw = re.search(r'```json\n(.*?)\n```', (root / 'README.md').read_text(), re.S).group(1)
    config = json.loads(raw)
    for role in config['models'].values():
        name = role['main']['token_env']
        assert step['env'][name] == '${{ secrets.' + name + ' }}'
        monkeypatch.setenv(name, 'runtime-secret-sentinel')
    path = tmp_path / 'readme.json'
    path.write_text(raw)
    runtime = load_runtime(path)
    assert set(runtime.choices) == {'translation', 'critic', 'repair'}
    assert runtime.secrets == ('runtime-secret-sentinel',) * 3


if __name__ == '__main__':
    boundary_main()
