"""T02: settings, mandatory CLI admission and Actions/container wiring."""
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from ydbdoc_review.config.loader import (
    AccessDenied,
    SettingsError,
    load_settings,
    require_actor,
)

ROOT = Path(__file__).resolve().parents[2]
VARIABLES = (
    'YDBDOC_MAX_DEPENDENCY_FILES_PER_ARTICLE',
    'YDBDOC_MAX_SOURCE_CHARACTERS',
    'YDBDOC_ALLOWED_ACTORS',
    'YDBDOC_DAILY_BUDGET_RUB',
)


@pytest.fixture
def settings_env():
    return dict(zip(VARIABLES, ('7', '1234', ' Alice , Bob ', '12.34'), strict=True)) | {
        'YDB_SA_KEY': 'SECRET-SENTINEL',
    }


def test_variable_values_whitespace_and_ydb(settings_env):
    settings = load_settings(settings_env)
    assert (settings.max_dependency_files, settings.max_source_characters) == (7, 1234)
    assert settings.daily_budget_rub == Decimal('12.34')
    require_actor(settings, 'aLiCe')
    require_actor(settings, 'Bob')
    assert settings.ydb_endpoint == 'grpcs://ydb.serverless.yandexcloud.net:2135'
    assert settings.ydb_database == '/ru-central1/b1g7gqj2vnq67gjseuva/etns0641qf73btm7j21k'
    assert settings.ydb_sa_key == 'SECRET-SENTINEL'
    assert 'SECRET-SENTINEL' not in repr(settings)
    settings_env.update(dict(zip(VARIABLES, ('0', '9876', 'Other', '0'), strict=True)))
    changed = load_settings(settings_env)
    assert changed.max_dependency_files == 0
    assert changed.max_source_characters == 9876
    assert changed.daily_budget_rub == 0
    with pytest.raises(AccessDenied, match='YDBDOC_ALLOWED_ACTORS'):
        require_actor(changed, 'Alice')


@pytest.mark.parametrize('variable', [*VARIABLES, 'YDB_SA_KEY'])
@pytest.mark.parametrize('value', [None, '', '   '])
def test_missing_values_fail_closed(settings_env, variable, value):
    if value is None:
        del settings_env[variable]
    else:
        settings_env[variable] = value
    with pytest.raises(SettingsError, match=variable):
        load_settings(settings_env)


@pytest.mark.parametrize('variable', [VARIABLES[0], VARIABLES[1]])
@pytest.mark.parametrize('value', ['-1', '1.5', 'nan', 'inf', 'true', 'secret-value'])
def test_integer_limits_reject_invalid_without_echo(settings_env, variable, value):
    settings_env[variable] = value
    with pytest.raises(SettingsError) as exc:
        load_settings(settings_env)
    assert variable in str(exc.value)
    assert value not in str(exc.value)


@pytest.mark.parametrize('value', ['-0.01', 'NaN', 'sNaN', 'Infinity', '-Infinity', 'secret-value'])
def test_budget_rejects_invalid_without_echo(settings_env, value):
    settings_env[VARIABLES[3]] = value
    with pytest.raises(SettingsError) as exc:
        load_settings(settings_env)
    assert VARIABLES[3] in str(exc.value)
    assert value not in str(exc.value)


def test_empty_actor_list_is_configuration_error(settings_env):
    settings_env[VARIABLES[2]] = ', ,'
    with pytest.raises(SettingsError, match='YDBDOC_ALLOWED_ACTORS'):
        load_settings(settings_env)


@pytest.mark.parametrize('actor', ['', None, 'Mallory'])
def test_acl_cannot_be_skipped(settings_env, actor):
    settings_env.update(YDBDOC_SKIP_OPS_GATES='1', YDBDOC_OPS_SKIP_GATES='true',
                        YDBDOC_TRANSCRIPT_BACKEND='null')
    with pytest.raises(AccessDenied):
        require_actor(load_settings(settings_env), actor)


def test_actions_examples_supply_exact_variables():
    action = yaml.safe_load((ROOT / 'action.yml').read_text())
    steps = action['runs']['steps']
    assert len(steps) == 1
    for variable in VARIABLES:
        assert steps[0]['env'][variable] == '${{ vars.' + variable + ' }}'
    for path in (ROOT / 'examples').glob('*.yml'):
        workflow = yaml.safe_load(path.read_text())
        action_steps = [step for job in workflow['jobs'].values() for step in job['steps']
                        if step.get('uses', '').startswith('ydb-platform/ydbdoc-review@')]
        assert action_steps, path
        for step in action_steps:
            for variable in VARIABLES:
                assert step['env'][variable] == '${{ vars.' + variable + ' }}'
            assert step['env']['YDB_SA_KEY'] == '${{ secrets.YDB_SA_KEY }}'


def test_docker_passes_variables_and_secret_by_name(settings_env, tmp_path):
    models = tmp_path / 'models.json'
    models.write_text(json.dumps({'models': {}}))
    capture = tmp_path / 'capture.json'
    docker = tmp_path / 'docker'
    docker.write_text('''#!/usr/bin/env python3
import json, os, sys
if sys.argv[1] == 'run':
    names = [sys.argv[i+1] for i, arg in enumerate(sys.argv[:-1]) if arg == '-e']
    with open(os.environ['CAPTURE'], 'w') as f:
        json.dump({'names': names, 'values': {n: os.environ.get(n) for n in names}}, f)
''')
    docker.chmod(0o755)
    env = os.environ | settings_env | {
        'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
        'GITHUB_ACTION_PATH': str(ROOT), 'GITHUB_WORKSPACE': str(tmp_path),
        'INPUT_CONFIG': str(models), 'CAPTURE': str(capture), 'YDBDOC_YDB_ENDPOINT': 'grpcs://example:2135',
        'YDBDOC_YDB_DATABASE': '/example', 'GITHUB_ACTOR': 'Alice',
        'YDBDOC_SKIP_OPS_GATES': 'true', 'YDBDOC_TRANSCRIPT_BACKEND': 's3',
    }
    result = subprocess.run(['bash', str(ROOT / 'action-docker.sh')], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    captured = json.loads(capture.read_text())
    for variable in (*VARIABLES, 'YDB_SA_KEY', 'YDBDOC_YDB_ENDPOINT', 'YDBDOC_YDB_DATABASE'):
        assert captured['values'][variable] == env[variable]
    assert 'YDBDOC_SKIP_OPS_GATES' not in captured['names']
    assert 'YDBDOC_TRANSCRIPT_BACKEND' not in captured['names']
    assert 'SECRET-SENTINEL' not in result.stdout + result.stderr


@pytest.mark.parametrize('mode', ['doc_translate', 'doc_verify', 'doc_continue'])
def test_entrypoint_preserves_variables(settings_env, tmp_path, mode):
    capture = tmp_path / 'capture.json'
    cli = tmp_path / 'ydbdoc-review'
    cli.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['CAPTURE'], 'w') as f:
    json.dump({'args': sys.argv[1:], 'env': dict(os.environ)}, f)
''')
    cli.chmod(0o755)
    env = os.environ | settings_env | {
        'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
        'CAPTURE': str(capture), 'INPUT_REPO_PATH': str(tmp_path),
        'INPUT_REPO': 'ydb-platform/ydb', 'INPUT_PR': '1', 'INPUT_MODE': mode, 'INPUT_CONFIG': '/tmp/models.json',
    }
    result = subprocess.run(['sh', str(ROOT / 'entrypoint.sh')], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    captured = json.loads(capture.read_text())
    assert captured['args'][:3] == [mode, '--repo', 'ydb-platform/ydb']
    for variable in (*VARIABLES, 'YDB_SA_KEY'):
        assert captured['env'][variable] == settings_env[variable]
    assert 'SECRET-SENTINEL' not in result.stdout + result.stderr


def test_ydb_overrides(settings_env):
    settings_env.update(YDBDOC_YDB_ENDPOINT='grpcs://custom:2135',
                        YDBDOC_YDB_DATABASE='/custom/db')
    settings = load_settings(settings_env)
    assert (settings.ydb_endpoint, settings.ydb_database) == ('grpcs://custom:2135', '/custom/db')


@pytest.mark.parametrize('variable,value', [
    ('YDBDOC_YDB_ENDPOINT', ''), ('YDBDOC_YDB_ENDPOINT', 'https://secret-value'),
    ('YDBDOC_YDB_DATABASE', ''), ('YDBDOC_YDB_DATABASE', 'secret-value'),
])
def test_invalid_ydb_settings(settings_env, variable, value):
    settings_env[variable] = value
    with pytest.raises(SettingsError, match=variable) as exc:
        load_settings(settings_env)
    assert 'secret-value' not in str(exc.value)
