"""Independent extensions at HTTP/SDK boundaries; real build and product CLI.

Reuses transport bookkeeping only. No runner, report, store or quality replacement.
"""
import json
import os
import runpy
from pathlib import Path
from urllib.parse import urlsplit

import requests
import ydb

from tests.contract import cli_boundary

_original_run = runpy.run_module


def run_product(*args, **kwargs):
    from ydbdoc_review import build, continuation, runner, verify

    root = Path(os.environ['T15_FIXTURE'])
    state_path = root / 'http.json'

    def read():
        return json.loads(state_path.read_text())

    def write(state):
        state_path.write_text(json.dumps(state))

    def actual_build(candidate):
        result = build.build_candidate(candidate)
        state = read()
        state.setdefault('actual_builds', []).append(
            dict(sha=candidate.sha, status=result.status, rc=result.returncode,
                 pages=sorted(result.anchors), log=result.log[-1500:]))
        write(state)
        return result

    for function in (runner.run_translate, verify.run_verify, continuation.run_continue):
        function.__kwdefaults__['build'] = actual_build

    original_pool = ydb.SessionPool

    def pool(driver):
        sql = original_pool(driver)
        if read().get('storage_error'):
            def unavailable(*args, **kwargs):
                raise RuntimeError('independent SDK storage unavailable')
            sql.retry_operation_sync = unavailable
        original_execute = sql.execute

        def execute(query, params, commit_tx):
            if 'SELECT run_id, entry_id, cost_rub FROM runs' in query:
                state = read()
                state['daily_queries'] = state.get('daily_queries', 0) + 1
                write(state)
            return original_execute(query, params, commit_tx)

        sql.execute = execute
        original_stop = sql.stop

        def stop():
            original_stop()
            if read().get('close_error'):
                raise RuntimeError('independent SDK close failure')

        sql.stop = stop
        return sql

    ydb.SessionPool = pool
    original_send = requests.Session.send

    def send(session, request, **kw):
        state = read()
        if 'model.invalid' in request.url and state.get('cancel_after'):
            state['cancel'] = len(state['model']) + 1 >= state['cancel_after']
            write(state)
        response = original_send(session, request, **kw)
        state = read()
        if request.url.endswith('/pulls/1') and state.get('merged'):
            data = response.json()
            data.update(merged=True, state='closed')
            response._content = json.dumps(data).encode()
        if urlsplit(request.url).path.endswith('/files') and state.get('files_error'):
            response.status_code = 503
            response._content = b'{"message":"independent files error"}'
        if 'model.invalid' in request.url and '/critic/' in request.url and state.get('critic_issue'):
            data = response.json()
            issue = dict(path='ydb/docs/en/a.md', problem='Independent retained issue',
                         expected_fix='Correct the heading', severity='error', source=None,
                         target=dict(start=1, end=1, quote='# Hello'))
            data['choices'][0]['message']['content'] = json.dumps(
                dict(complete=True, verdict='issues', issues=[issue]))
            response._content = json.dumps(data).encode()
        if 'model.invalid' in request.url and state.get('fallback_once'):
            state['fallback_once'] = False
            write(state)
            response.status_code = 503
            response._content = json.dumps(dict(error=dict(message='paid unavailable'),
                usage=dict(prompt_tokens=10, completion_tokens=5))).encode()
        return response

    requests.Session.send = send
    return _original_run(*args, **kwargs)


if __name__ == '__main__':
    runpy.run_module = run_product
    cli_boundary.main()
