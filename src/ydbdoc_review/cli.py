"""The three product entry points, wired to the single production pipeline."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from functools import partial
from pathlib import Path
from uuid import uuid4

from ydbdoc_review.config.loader import load_settings, require_actor
from ydbdoc_review.config.runtime import load_runtime
from ydbdoc_review.diagnostics import redact_known
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.github.git_ops import remote_push_url
from ydbdoc_review.plan import freeze_snapshot
from ydbdoc_review.publication import Publisher
from ydbdoc_review.report import create_reporter
from ydbdoc_review.runner import RunHooks, RunResult, finalize, run_translate
from ydbdoc_review.shutdown import install_shutdown_handlers, is_shutdown_requested
from ydbdoc_review.store import ContextExpired, RunStore, create_store
from ydbdoc_review.verify import run_verify

MODES = ('doc_translate', 'doc_verify', 'doc_continue')
MODE_ALIASES = {'run': 'doc_translate', 'verify': 'doc_verify', 'continue': 'doc_continue'}


def fetch_snapshot(repo: Path, github: GitHubClient, identity: str, token: str):
    owner, name, number = identity.split('/')
    snapshot = freeze_snapshot(github, owner, name, int(number))
    url = remote_push_url(f'https://github.com/{snapshot.source_repo}.git', token)
    proc = subprocess.run(['git', '-C', str(repo), 'fetch', '--depth=1', '--no-tags', '--', url,
                           snapshot.source_sha], capture_output=True, timeout=120)
    if proc.returncode:
        # stderr may contain remote credentials; do not publish it.
        raise RuntimeError(f'Cannot fetch immutable source SHA {snapshot.source_sha}')
    return snapshot


def execute(mode: str, repository: str, pr: int, config: Path | None = None) -> RunResult:
    token = os.environ.get('GITHUB_TOKEN', '')
    push_token = os.environ.get('GITHUB_PUSH_TOKEN') or token
    if not token:
        raise ValueError('GITHUB_TOKEN is required')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or pr <= 0:
        raise ValueError('Expected repository owner/name and positive PR number')
    github = GitHubClient(token)
    current = f'{repository}/{pr}'
    secrets = (token, push_token, os.environ.get('YDB_SA_KEY', ''))
    # Only refusal delivery is possible before ACL/config admission. Constructing
    # this callback performs no HTTP and grants no model/Git/store capability.
    reporter = create_reporter(github, current_pr=current, authorized=True, secrets=secrets)
    store = None
    adapter = None
    try:
        settings = load_settings()
        actor = os.environ.get('GITHUB_ACTOR', '')
        require_actor(settings, actor)
        runtime = load_runtime(config)
        secrets = (*runtime.secrets, *secrets)
        reporter = create_reporter(github, current_pr=current, authorized=True, secrets=secrets)
        store = create_store(endpoint=settings.ydb_endpoint, database=settings.ydb_database)
        adapter = RunStore(store, mode=mode, source_pr=current)
        context = None
        if mode in ('doc_continue', 'doc_verify'):
            try:
                context = store.latest_context(current)
            except ContextExpired:
                if mode == 'doc_continue':
                    raise
            if context:
                reporter = create_reporter(github, current_pr=current,
                                           source_pr=context['original_pr'],
                                           authorized=True, secrets=secrets)
        with tempfile.TemporaryDirectory(prefix='ydbdoc-run-') as directory:
            repo = Path(directory)
            subprocess.run(['git', 'init', '--bare', str(repo)], check=True,
                           capture_output=True, timeout=30)
            snapshot = fetch_snapshot(repo, github, current, token)
            snapshots = {current: snapshot}

            def fetched(identity):
                if identity not in snapshots:
                    snapshots[identity] = fetch_snapshot(repo, github, identity, token)
                return snapshots[identity]

            target_identity = current
            if mode == 'doc_continue':
                source_snapshot = fetched(context['source_pr'])
                receipt = context['result']['publication']
                target_identity = f"{receipt['repository']}/{receipt['pr_number']}"
                snapshot = fetched(target_identity)
            target_repo, target_number = target_identity.rsplit('/', 1)
            publisher = Publisher(github, snapshot.source_repo,
                                  f'https://github.com/{snapshot.source_repo}.git',
                                  f'ydbdoc-review/pr-{pr}-{uuid4().hex[:12]}' if mode == 'doc_translate'
                                  else snapshot.publication_base, push_token,
                                  pr_number=None if mode == 'doc_translate' else int(target_number),
                                  pr_repository=None if mode == 'doc_translate' else target_repo)
            owner, name = repository.split('/')
            options = dict(repo=repo, github=github, owner=owner, repository=name,
                           pr_number=pr, actor=actor, settings=settings, publisher=publisher,
                           critic_choice=runtime.choices['critic'], repair_choice=runtime.choices['repair'],
                           budget=runtime.budget)
            model_options = dict(cost_resolver=runtime.resolver, timeout_s=runtime.timeout_s)
            if mode == 'doc_continue':
                from ydbdoc_review.continuation import run_continue
                return run_continue(**options, store=store, model_options=model_options,
                                    source_snapshot=source_snapshot, target_snapshot=snapshot,
                                    hooks=RunHooks(report=reporter, cancelled=is_shutdown_requested, secrets=secrets))
            options.update(snapshot=snapshot, model_factory=partial(adapter.model_factory, **model_options),
                           admit=partial(adapter.admit, settings.daily_budget_rub),
                           hooks=adapter.hooks(report=reporter, cancelled=is_shutdown_requested, secrets=secrets),
                           glossary=runtime.glossary)
            if mode == 'doc_translate':
                return run_translate(**options, translation_choice=runtime.choices['translation'])
            return run_verify(**options)
    except (Exception, KeyboardInterrupt) as exc:
        message = f'{type(exc).__name__}: {exc}'
        message = redact_known(message, secrets)
        result = RunResult(mode=mode, status='RED', message=message,
                           errors=(message,), cancelled=isinstance(exc, KeyboardInterrupt))
        return finalize(result, adapter.hooks(report=reporter, secrets=secrets) if adapter else
                        RunHooks(report=reporter, secrets=secrets))
    finally:
        if store is not None:
            try:
                store.close()
            except (Exception, KeyboardInterrupt) as exc:
                # Storage/report finalization has already completed. Closing an
                # SDK resource cannot change the checked/published verdict.
                message = f'Cleanup warning (YDB close): {type(exc).__name__}: {exc}'
                message = redact_known(message, secrets)
                print(message, file=sys.stderr)


def app(argv=None):
    parser = argparse.ArgumentParser(description='YDB documentation translation and verification')
    parser.add_argument('mode', choices=(*MODES, *MODE_ALIASES), nargs='?', default='doc_translate')
    parser.add_argument('--repo', required=True, help='GitHub owner/name')
    parser.add_argument('--pr', required=True, type=int)
    parser.add_argument('--config', type=Path, help='Optional trusted technical model JSON configuration')
    args = parser.parse_args(argv)
    install_shutdown_handlers()
    try:
        result = execute(MODE_ALIASES.get(args.mode, args.mode), args.repo, args.pr, args.config)
    except (Exception, KeyboardInterrupt) as exc:
        print(str(exc))
        raise SystemExit(1) from None
    print(f'{result.status}: {result.message}')
    raise SystemExit(0 if result.status in ('GREEN', 'NO_WORK') else 1)
