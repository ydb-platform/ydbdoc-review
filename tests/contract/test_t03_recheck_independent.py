"""Independent re-acceptance of PR diff metadata through actual Git snapshots."""
from types import SimpleNamespace

import pytest

from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.plan import PlanError, build_plan, freeze_snapshot, list_changes, read_at_sha


@pytest.mark.parametrize('language', ['ru', 'en'])
@pytest.mark.parametrize('merged', [False, True])
@pytest.mark.parametrize('edited', [False, True])
def test_actual_api_metadata_and_git_tree(git_repo, monkeypatch, language, merged, edited):
    repo, git = git_repo
    old, new = [f'ydb/docs/{language}/{name}.md' for name in ('old', 'new')]
    (repo / old).parent.mkdir(parents=True)
    (repo / old).write_text('line one\nline two\nline three\n')
    git('add', '.')
    git('commit', '-m', 'seed')
    common = git('rev-parse', 'HEAD').decode().strip()
    git('checkout', '-b', 'feature')
    git('mv', old, new)
    expected = 'line one\nline two\nline three\n' + ('added line\n' if edited else '')
    (repo / new).write_text(expected)
    git('add', '.')
    git('commit', '-m', 'rename')
    head = git('rev-parse', 'HEAD').decode().strip()
    nums = git('diff', '--numstat', '--find-renames', common, head).decode().split('\t')
    additions, deletions = int(nums[0]), int(nums[1])
    assert bool(additions + deletions) is edited
    git('checkout', 'main')
    if merged:
        git('merge', '--ff-only', 'feature')
    else:
        (repo / old).write_text('different main content\n')
        git('commit', '-am', 'diverge main')
    base = git('rev-parse', 'HEAD').decode().strip()
    info = SimpleNamespace(
        get_pull=lambda *a: {'merged': merged, 'state': 'closed' if merged else 'open',
                            'head': {'sha': head, 'ref': 'feature', 'repo': {'full_name': 'o/r'}},
                            'base': {'sha': base, 'ref': 'main'}},
        get_branch_sha=lambda *a: base,
    )
    frozen = freeze_snapshot(info, 'o', 'r', 1)
    assert not hasattr(frozen, 'comparison_sha')
    assert read_at_sha(str(repo), frozen.source_sha, old) is None
    api = GitHubClient('fake')
    monkeypatch.setattr(api, '_request', lambda *a, **kw: [
        {'filename': new, 'previous_filename': old, 'status': 'renamed',
         'changes': additions + deletions, 'additions': additions, 'deletions': deletions},
    ])
    calls = []
    def read(sha, path):
        calls.append((sha, path))
        assert path == new
        return read_at_sha(str(repo), sha, path)
    plan = build_plan(frozen, list_changes(api, 'o', 'r', 1), read)
    assert calls == [(head, new)]
    assert plan.files == {new: expected}
    assert plan.needs_model is edited
    assert not plan.no_work
    assert plan.operations[0].target_old_path == old.replace(f'/{language}/', '/en/' if language == 'ru' else '/ru/')


@pytest.mark.parametrize('edited', [False, True])
def test_valid_metadata_does_not_hide_missing_selected_source(edited):
    sha = 'a' * 40
    info = SimpleNamespace(get_pull=lambda *a: {
        'head': {'sha': sha, 'ref': 'feature', 'repo': {'full_name': 'o/r'}},
        'base': {'sha': sha, 'ref': 'main'},
    })
    frozen = freeze_snapshot(info, 'o', 'r', 1)
    api = SimpleNamespace(iter_pull_files=lambda *a: iter([
        {'filename': 'ydb/docs/en/new.md', 'previous_filename': 'ydb/docs/en/old.md',
         'status': 'renamed', 'changes': int(edited), 'additions': int(edited), 'deletions': 0},
    ]))
    with pytest.raises(PlanError, match=r'en/new.md.*файл отсутствует'):
        build_plan(frozen, list_changes(api, 'o', 'r', 1), lambda *a: None)
