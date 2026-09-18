"""The deployment fetch needs one immutable tree, not the monorepo history."""
import subprocess
from types import SimpleNamespace

from ydbdoc_review import cli


def test_deployment_fetch_is_shallow_and_keeps_source_tree(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(['git', *map(str, args)], check=True, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    origin = tmp_path / 'origin'
    origin.mkdir()
    git('init', origin)
    git('-C', origin, 'config', 'user.name', 'Fixture')
    git('-C', origin, 'config', 'user.email', 'fixture@example.test')
    for i in range(3):
        (origin / 'source.md').write_text(f'Version {i}\n')
        git('-C', origin, 'add', 'source.md')
        git('-C', origin, 'commit', '-m', str(i))
    sha = git('-C', origin, 'rev-parse', 'HEAD')
    target = tmp_path / 'target'
    git('init', '--bare', target)
    snapshot = SimpleNamespace(source_repo='owner/repo', source_sha=sha)
    monkeypatch.setattr(cli, 'freeze_snapshot', lambda *args: snapshot)
    monkeypatch.setattr(cli, 'remote_push_url', lambda *args: origin.as_uri())
    assert cli.fetch_snapshot(target, object(), 'owner/repo/1', 'fake') is snapshot
    assert git('-C', target, 'rev-list', '--count', sha) == '1'
    assert git('-C', target, 'show', f'{sha}:source.md') == 'Version 2'
