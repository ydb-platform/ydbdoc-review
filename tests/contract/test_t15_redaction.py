"""Known credentials at diagnostic boundaries; real CLI and finalization."""
import json
import os
import runpy
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests

from tests.contract.test_t15_cli import assert_saved, process  # noqa: F401
from tests.contract.test_t15_independent import (  # noqa: F401
    MODES,
    independent,
    source_only,
    successful,
)
from ydbdoc_review.diagnostics import redact_known
from ydbdoc_review.model import AttemptRecord, RequestRecord, Usage
from ydbdoc_review.runner import RunHooks, RunResult, finalize


def boundary_main():
    from tests.contract import test_t15_recheck

    original_run = runpy.run_module

    def launch(*args, **kwargs):
        path = Path(os.environ['T15_FIXTURE']) / 'http.json'
        original_send = requests.Session.send

        def send(session, request, **kw):
            state = json.loads(path.read_text())
            phase = state.get('redaction_phase')
            is_comment = request.method == 'POST' and request.url.endswith('/comments')
            if is_comment:
                state['report_attempts'] = state.get('report_attempts', 0) + 1
                path.write_text(json.dumps(state))
            fail = ((phase == 'metadata' and request.url.endswith('/pulls/1')) or
                    (phase == 'report' and is_comment and
                    state['report_attempts'] == state['fail_report_number']))
            if fail:
                state['http'].append([request.method, urlsplit(request.url).path])
                path.write_text(json.dumps(state))
                message = 'transport diagnostic ' + ' '.join(os.environ[name] for name in
                    ('GITHUB_TOKEN', 'GITHUB_PUSH_TOKEN', 'MODEL_TOKEN'))
                if state['redaction_fault'] == 'interrupt':
                    raise KeyboardInterrupt(message)
                if state['redaction_fault'] == 'transport':
                    raise requests.ConnectionError(message)
                response = requests.Response()
                response.status_code = 403
                response._content = message.encode()
                return response
            return original_send(session, request, **kw)

        requests.Session.send = send
        return original_run(*args, **kwargs)

    runpy.run_module = launch
    test_t15_recheck.boundary_main()


def invoke(p, mode, pr=1):
    return subprocess.run([sys.executable, '-m', 'tests.contract.test_t15_redaction', mode,
        '--repo', 'up/docs', '--pr', str(pr), '--config', str(p.root / 'models.json')],
        cwd=Path(__file__).resolve().parents[2], env=p.env, text=True,
        capture_output=True, timeout=110)


def credentials(p):
    # Distinct arbitrary values, including regex syntax; no sentinel/prefix policy.
    values = ('fixture.github+91', 'fixture.push[82]', 'fixture.model(73)')
    p.env.update(zip(('GITHUB_TOKEN', 'GITHUB_PUSH_TOKEN', 'MODEL_TOKEN'), values, strict=True))
    # Route only these synthetic credentials to the existing local bare fixture.
    wrapper = p.root / 'tools/git'
    command = wrapper.read_text()
    options = ' '.join('-c ' + shlex.quote(
        f'url.{p.remote}.insteadOf=https://x-access-token:{value}@github.com/up/docs.git')
        for value in values[:2])
    wrapper.write_text(command.replace(' "$@"', ' ' + options + ' "$@"'))
    return values


def assert_private(result, state, values, context):
    public = result.stdout + result.stderr + str(state['comments'])
    public += context['result']['message'] + str(context['result']['errors'])
    for value in values:
        assert value not in public
    assert '[REDACTED]' in result.stdout


@pytest.mark.parametrize('mode', MODES[:2])
@pytest.mark.parametrize('fault', ['transport', 'interrupt'])
def test_metadata_transport_error_redacts_all_known_credentials(independent, mode, fault):  # noqa: F811
    p = independent
    values = credentials(p)
    p.update(redaction_phase='metadata', redaction_fault=fault)
    result = invoke(p, mode)
    assert result.returncode == 1 and result.stdout.startswith('RED:')
    context, attempts = assert_saved(p, mode, 'RED')
    assert_private(result, p.read(), values, context)
    assert ('KeyboardInterrupt' if fault == 'interrupt' else 'ConnectionError') in result.stdout
    assert context['result']['cancelled'] == (fault == 'interrupt')
    assert not attempts and not p.read()['model'] and not p.read().get('factories', 0)
    assert p.read()['report_attempts'] == 1 and len(p.read()['comments']) == 1
    assert len(p.read()['pulls']) == 1


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('fault', ['transport', 'http', 'interrupt'])
def test_last_report_failure_preserves_result_cost_and_no_retries(independent, mode, fault):  # noqa: F811
    p = independent
    if mode != 'doc_verify':
        source_only(p)
    if mode == 'doc_continue':
        successful(p.run('doc_translate'))
    values = credentials(p)
    reports = 1 if mode == 'doc_verify' else 2
    p.update(redaction_phase='report', redaction_fault=fault, fail_report_number=reports,
             model=[], comments=[], http=[], stops=[])
    result = invoke(p, mode, 2 if mode == 'doc_continue' else 1)
    assert result.returncode == 1 and result.stdout.startswith('RED:')
    context, attempts = assert_saved(p, mode, 'RED')
    state = p.read()
    assert_private(result, state, values, context)
    kind = {'transport': 'ConnectionError', 'http': 'GitHubAPIError', 'interrupt': 'KeyboardInterrupt'}[fault]
    assert f'report: {kind}' in result.stdout
    if fault == 'http':
        assert 'HTTP 403' in result.stdout
    assert context['result']['cancelled'] == (fault == 'interrupt')
    roles = {'doc_translate': ['translation', 'critic'], 'doc_verify': ['critic'],
             'doc_continue': ['critic', 'repair', 'critic']}[mode]
    assert [role for role, _ in state['model']] == roles
    assert len(attempts) == len(roles) == len({row['entry_id'] for row in attempts})
    assert context['result']['cost_breakdown']['total'] == Decimal('.25') * len(roles)
    assert state['report_attempts'] == reports and len(state['comments']) == reports - 1
    assert state['stops'] == ['pool', 'driver']
    assert state['git_pushes'] == 1
    assert state['pulls']['2' if mode != 'doc_verify' else '1']['draft']
    assert context['result']['checked_sha'] == context['result_sha']


@pytest.mark.parametrize('phase', ['storage', 'report', 'storage final outcome', 'cancellation'])
@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
def test_finalization_redacts_each_error_without_touching_raw_attempts(phase, error_type):
    secrets = ('arbitrary-a+b', 'arbitrary-a+b-long', 'model[credential]')
    raw = 'raw response ' + ' / '.join(secrets)
    request = RequestRecord('attempt-id', 'critic', 'eliza', 'critic', 'https://model.invalid',
                            {'prompt': raw}, datetime.now(UTC), 0)
    attempt = AttemptRecord(request, raw, 200, None, Usage(cost_rub=Decimal('.25')))
    original = RunResult(status='GREEN', message='initial ' + raw, errors=(raw,), attempts=(attempt,))
    saved, reports = [], []
    def save(result):
        saved.append(result)
        if phase == 'storage' or (phase == 'storage final outcome' and len(saved) == 2):
            raise error_type(raw)
    def report(result):
        reports.append(result)
        if phase in ('report', 'storage final outcome'):
            raise error_type(raw)
    def cancelled():
        if phase == 'cancellation':
            raise error_type(raw)
        return False
    result = finalize(original, RunHooks(save=save, report=report, cancelled=cancelled, secrets=secrets))
    assert result.status == 'RED' and result.cancelled == (error_type is KeyboardInterrupt)
    assert f'{phase}: {error_type.__name__}' in result.message
    assert len(reports) == 1
    assert len(saved) == (1 if phase == 'cancellation' and error_type is KeyboardInterrupt else 2)
    for value in [result, *saved, *reports]:
        assert value.attempts == original.attempts  # §4 raw responses are retained.
        for secret in secrets:
            assert secret not in value.message + str(value.errors)
    assert original.message.endswith(raw)


def test_known_secret_replacement_is_literal_longest_first_and_single_pass():
    assert redact_known('a+b-long a+b [REDACTED]', ('', 'a+b', 'a+b-long', 'REDACTED')) == (
        '[REDACTED] [REDACTED] [[REDACTED]]')
    assert redact_known('HTTP 403 ConnectionError', ()) == 'HTTP 403 ConnectionError'


if __name__ == '__main__':
    boundary_main()
