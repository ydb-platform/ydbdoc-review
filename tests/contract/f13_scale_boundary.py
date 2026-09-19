"""Offline scale adapters; HTTP/YDB bookkeeping reused from the CLI boundary.

The build is a declared mock. Its large log and failed anchor validation are
inputs to real linking, reporting, persistence and continuation code.
"""
import json
import os
import runpy
import subprocess
import time
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

    def builder(candidate):
        state = json.loads(state_path.read_text())
        state['builds'].append(candidate.sha)
        state.setdefault('tree_sizes', []).append(len(candidate.entries))
        state_path.write_text(json.dumps(state))
        failed = state.get('scale_build_failure', False)
        log = '\n'.join(f'WARN: fixture warning {i}' for i in range(8894))
        if failed:
            log += '\nERROR: simulated documentation build failure\n'
        return build.BuildResult(candidate.sha, 'failure' if failed else 'success',
                                 log, 1 if failed else 0,
                                 anchors={'ydb/docs/en/a.md': frozenset({'hello'}),
                                          'ydb/docs/en/b.md': frozenset({'hello'})})

    original_pool = ydb.SessionPool

    def pool(driver):
        sql = original_pool(driver)
        execute = sql.execute

        def guarded_execute(query, params, commit_tx):
            state = json.loads(state_path.read_text())
            key = params.get('$object_key', '')
            if 'UPSERT INTO run_objects' in query and key.startswith('documents/'):
                state['document_writes'] = state.get('document_writes', 0) + 1
                state_path.write_text(json.dumps(state))
                if state.get('scale_store_failure'):
                    raise OSError('F13 final document storage unavailable')
            return execute(query, params, commit_tx)

        sql.execute = guarded_execute
        return sql

    ydb.SessionPool = pool
    original_send = requests.Session.send

    def send(session, request, **kw):
        state = json.loads(state_path.read_text())
        path = urlsplit(request.url).path
        data = json.loads(request.body) if request.body else {}
        if state.get('scale_report_failure') and request.method == 'POST' and path.endswith('/issues/1/comments'):
            state['http'].append([request.method, path])
            state_path.write_text(json.dumps(state))
            response = requests.Response()
            response.status_code = 503
            response._content = b'{"message":"F13 source report unavailable"}'
            return response
        if 'body' in data:
            assert len(data['body'].encode('utf-8')) < 65536
        response = original_send(session, request, **kw)
        state = json.loads(state_path.read_text())
        if 'model.invalid' in request.url and state.get('reasoning_only'):
            data = response.json()
            data['choices'] = [dict(message=dict(content='', reasoning_content=
                'F13_REASONING_MUST_NOT_BECOME_TRANSLATION'), finish_reason='length')]
            response._content = json.dumps(data).encode()
        return response

    requests.Session.send = send
    for function in (runner.run_translate, verify.run_verify, continuation.run_continue):
        function.__kwdefaults__['build'] = builder
    original_subprocess_run = subprocess.run
    git_counts = {}
    git_seconds = {}

    def observed_run(command, *run_args, **run_kwargs):
        is_git = isinstance(command, (list, tuple)) and Path(str(command[0])).name == 'git'
        operation = next((part for part in command if part in {'cat-file', 'ls-tree', 'fetch', 'push', 'clone'}), 'other') if is_git else None
        start = time.monotonic()
        try:
            return original_subprocess_run(command, *run_args, **run_kwargs)
        finally:
            if is_git:
                git_counts[operation] = git_counts.get(operation, 0) + 1
                git_seconds[operation] = git_seconds.get(operation, 0.0) + time.monotonic() - start
                if sum(git_counts.values()) % 1000 == 0:
                    (root / 'git-progress.json').write_text(json.dumps(
                        dict(calls=git_counts, seconds=git_seconds)))

    subprocess.run = observed_run
    try:
        return _original_run(*args, **kwargs)
    finally:
        state = json.loads(state_path.read_text())
        state['git_subprocess_calls'] = git_counts
        state['git_subprocess_seconds'] = {key: round(value, 3) for key, value in git_seconds.items()}
        state_path.write_text(json.dumps(state))



if __name__ == '__main__':
    runpy.run_module = run_product
    cli_boundary.main()
