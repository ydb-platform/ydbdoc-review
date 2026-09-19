"""Production CLI keeps the fetched snapshot even when merged base advances."""
import json
import subprocess
import sys

import pytest

from tests.contract.test_t15_cli import ROOT, assert_saved

pytest_plugins = ['tests.contract.test_t15_cli']


@pytest.mark.timeout(60)
def test_merged_base_moves_after_fetch_without_replacing_snapshot(process):
    p = process
    (p.repo / 'ydb/docs/en/a.md').unlink()
    p.git('add', '.')
    p.git('commit', '-m', 'source only')
    source_sha = p.git('rev-parse', 'HEAD').decode().strip()
    p.git('push', str(p.remote), source_sha + ':refs/heads/main')
    # The next base response names an unfetched commit, as in the live failure.
    result = subprocess.run(
        [sys.executable, '-m', 'tests.contract.test_snapshot_once_t15',
         'doc_translate', '--repo', 'up/docs', '--pr', '1',
         '--config', str(p.root / 'models.json')],
        env=p.env, cwd=ROOT, capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    context, _ = assert_saved(p, 'doc_translate', 'GREEN')
    assert context['source_sha'] == source_sha
    assert context['source_sha'] == source_sha
    assert p.read()['pulls']['2']['base'] == 'main'
    assert context['result']['checked_sha'] == context['result_sha']
    assert json.loads((p.root / 'snapshot_reads.json').read_text()) == {'base_reads': 1}


@pytest.mark.timeout(60)
@pytest.mark.parametrize('mode', ['doc_verify', 'doc_continue'])
def test_cli_reuses_snapshot_with_existing_pr_publication(process, mode):
    p = process
    if mode == 'doc_continue':
        seed = p.invoke('doc_translate')
        assert seed.returncode == 0, seed.stdout + seed.stderr
        p.update(http=[])
    result = p.invoke(mode, 2 if mode == 'doc_continue' else 1)
    assert result.returncode == 0, result.stdout + result.stderr
    context, _ = assert_saved(p, mode, 'GREEN')
    reads = [path for method, path in p.read()['http'] if method == 'GET']
    # One snapshot read, one publication preflight, one publication receipt.
    target = 2 if mode == 'doc_continue' else 1
    assert reads.count(f'/repos/up/docs/pulls/{target}') == 3
    if mode == 'doc_continue':
        assert reads.count('/repos/up/docs/pulls/1') == 1
    assert context['result']['checked_sha'] == context['result_sha']


def moving_base_boundary():
    import os
    from pathlib import Path

    from tests.contract.cli_boundary import main
    from ydbdoc_review.github.client import GitHubClient

    original_pull = GitHubClient.get_pull
    original_branch = GitHubClient.get_branch_sha
    reads = 0

    def get_pull(self, owner, repo, number):
        pull = original_pull(self, owner, repo, number)
        if number == 1:
            pull.update(merged=True, state='closed')
        return pull

    def get_branch(self, owner, repo, branch):
        nonlocal reads
        if branch == 'main':
            reads += 1
            (Path(os.environ['T15_FIXTURE']) / 'snapshot_reads.json').write_text(
                json.dumps({'base_reads': reads}))
            if reads > 1:
                return 'f' * 40
        return original_branch(self, owner, repo, branch)

    GitHubClient.get_pull = get_pull
    GitHubClient.get_branch_sha = get_branch
    main()


if __name__ == '__main__':
    moving_base_boundary()
