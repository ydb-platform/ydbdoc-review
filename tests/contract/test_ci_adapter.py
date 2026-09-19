"""Actual shell boundary and production config with the deployed workflow inputs."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from ydbdoc_review import cli
from ydbdoc_review.config.loader import SettingsError
from ydbdoc_review.config.runtime import load_runtime
from ydbdoc_review.runner import RunResult

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cloud_env(monkeypatch):
    monkeypatch.setenv('YANDEX_CLOUD_FOLDER_DOC_REVIEW', 'test-folder')
    monkeypatch.setenv('YANDEX_CLOUD_API_KEY_DOC_REVIEW', 'secret-sentinel')
    for key in ('YDBDOC_MODEL_PROVIDER', 'YDBDOC_MODEL_TRANSLATE', 'YDBDOC_MODEL_CHECK'):
        monkeypatch.setenv(key, '')


def test_existing_workflow_defaults_and_model_overrides(cloud_env, monkeypatch):
    runtime = load_runtime()
    assert runtime.choices['translation'].main.model == 'deepseek-v4-flash'
    assert runtime.choices['translation'].alternative.model == 'yandexgpt-5-pro'
    assert runtime.choices['critic'].main.model == 'yandexgpt-5.1'
    assert runtime.choices['critic'].alternative.model == 'yandexgpt-5-lite'
    assert runtime.choices['repair'] == runtime.choices['translation']
    assert runtime.choices['translation'].main.folder_id == 'test-folder'
    monkeypatch.setenv('YDBDOC_MODEL_TRANSLATE', 'yandexgpt-5-pro')
    monkeypatch.setenv('YDBDOC_MODEL_CHECK', 'yandexgpt-5-lite')
    runtime = load_runtime()
    assert runtime.choices['translation'].main.model == 'yandexgpt-5-pro'
    assert runtime.choices['translation'].alternative is None
    assert runtime.choices['critic'].main.model == 'yandexgpt-5-lite'
    assert runtime.choices['critic'].alternative is None


def test_missing_cloud_secret_is_clear_config_failure(cloud_env, monkeypatch):
    monkeypatch.delenv('YANDEX_CLOUD_API_KEY_DOC_REVIEW')
    with pytest.raises(SettingsError):
        load_runtime()


@pytest.mark.parametrize('input_mode,expected', [('', 'doc_translate'), ('run', 'doc_translate'),
    ('verify', 'doc_verify'), ('continue', 'doc_continue'), ('doc_translate', 'doc_translate')])
def test_shell_entrypoint_without_config(tmp_path, input_mode, expected):
    executable = tmp_path / 'ydbdoc-review'
    executable.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
    executable.chmod(0o755)
    env = os.environ | {'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
                        'INPUT_MODE': input_mode, 'INPUT_REPO': 'ydb-platform/ydb',
                        'INPUT_PR': '51079', 'INPUT_CONFIG': ''}
    result = subprocess.run(['sh', str(ROOT / 'entrypoint.sh')], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [expected, '--repo', 'ydb-platform/ydb', '--pr', '51079']


@pytest.mark.parametrize('mode,expected', [('run', 'doc_translate'), ('verify', 'doc_verify'),
                                         ('continue', 'doc_continue'), ('', 'doc_translate')])
def test_cli_alias_without_config(monkeypatch, mode, expected):
    calls = []
    def execute(*args):
        calls.append(args)
        return RunResult(mode=expected, status='NO_WORK', message='empty')
    monkeypatch.setattr(cli, 'execute', execute)
    monkeypatch.setattr(cli, 'install_shutdown_handlers', lambda: None)
    with pytest.raises(SystemExit) as exit:
        cli.app(([mode] if mode else []) + ['--repo', 'ydb-platform/ydb', '--pr', '51079'])
    assert exit.value.code == 0
    assert calls == [(expected, 'ydb-platform/ydb', 51079, None)]


@pytest.mark.parametrize('config', ['', 'invalid'])
def test_docker_without_config_forwards_existing_secrets(tmp_path, config):
    docker = tmp_path / 'docker'
    capture = tmp_path / 'capture.json'
    docker.write_text('''#!/usr/bin/env python3
import json,os,sys
if sys.argv[1] == 'run':
    names = [sys.argv[i+1] for i,arg in enumerate(sys.argv[:-1]) if arg == '-e']
    with open(os.environ['CAPTURE'], 'w') as stream:
        json.dump({name:os.environ.get(name) for name in names},stream)
''')
    docker.chmod(0o755)
    env = os.environ | {'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
        'CAPTURE': str(capture), 'GITHUB_ACTION_PATH': str(ROOT),
        'GITHUB_WORKSPACE': str(tmp_path), 'INPUT_CONFIG': config,
        'YANDEX_CLOUD_FOLDER_DOC_REVIEW': 'folder',
        'YANDEX_CLOUD_API_KEY_DOC_REVIEW': 'secret-sentinel',
        'YDBDOC_MODEL_TRANSLATE': 'deepseek-v32', 'YDBDOC_MODEL_CHECK': 'yandexgpt-5.1'}
    result = subprocess.run(['bash', str(ROOT / 'action-docker.sh')], env=env,
                            capture_output=True, text=True, timeout=30)
    if config:
        assert result.returncode != 0
        assert not capture.exists()
    else:
        assert result.returncode == 0, result.stderr
        received = json.loads(capture.read_text())
        for key in ('YANDEX_CLOUD_FOLDER_DOC_REVIEW', 'YANDEX_CLOUD_API_KEY_DOC_REVIEW',
                    'YDBDOC_MODEL_TRANSLATE', 'YDBDOC_MODEL_CHECK'):
            assert received[key] == env[key]
    assert 'secret-sentinel' not in result.stdout + result.stderr


def test_explicit_config_overrides_defaults_and_preserves_tariffs(tmp_path, monkeypatch):
    monkeypatch.setenv('CUSTOM_TOKEN', 'custom-secret')
    endpoint = dict(provider='eliza', base_url='https://example.invalid',
                    model='custom', token_env='CUSTOM_TOKEN')
    path = tmp_path / 'models.json'
    path.write_text(json.dumps(dict(
        models={role: {'main': endpoint} for role in ('translation', 'critic', 'repair')},
        context_tokens=16000, max_output_tokens=2000,
        tariffs_rub_per_million=[dict(provider='eliza', model='custom', input='10', output='20')],
    )))
    runtime = load_runtime(path)
    primary = runtime.choices['translation'].main
    assert primary.provider == 'eliza'
    assert primary.token == 'custom-secret'
    assert runtime.resolver(primary, {'usage': {'prompt_tokens': 1000000, 'completion_tokens': 1000000}}) == 30
