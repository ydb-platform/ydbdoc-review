"""Independent T03 adversarial acceptance tests; no production edits."""
from functools import partial
from types import SimpleNamespace

import pytest

from ydbdoc_review.plan import ChangedFile, PlanError, build_plan, freeze_snapshot, list_changes, read_at_sha

OLD = 'ydb/docs/en/old.md'
NEW = 'ydb/docs/en/new.md'


def client(head, base, merged=False):
    return SimpleNamespace(
        get_pull=lambda *a: {'state': 'closed' if merged else 'open', 'merged': merged,
                            'head': {'sha': head, 'ref': 'feature', 'repo': {'full_name': 'o/r'}},
                            'base': {'sha': base, 'ref': 'main'}},
        get_branch_sha=lambda *a: base,
    )


def seed(repo, git):
    file = repo / OLD
    file.parent.mkdir(parents=True)
    file.write_text('original source\n')
    git('add', '.')
    git('commit', '-m', 'original')
    return git('rev-parse', 'HEAD').decode().strip()


def test_pure_rename_after_base_advances_stays_mechanical(git_repo):
    repo, git = git_repo
    common = seed(repo, git)
    git('checkout', '-b', 'feature')
    git('mv', OLD, NEW)
    git('commit', '-m', 'pure rename')
    head = git('rev-parse', 'HEAD').decode().strip()
    git('checkout', 'main')
    (repo / OLD).write_text('unrelated newer main content\n')
    git('commit', '-am', 'main advanced')
    base = git('rev-parse', 'HEAD').decode().strip()
    assert git('diff', '--name-status', common, head).decode().startswith('R100')
    frozen = freeze_snapshot(client(head, base), 'o', 'r', 1)
    plan = build_plan(frozen, [ChangedFile(NEW, 'renamed', OLD, content_changed=False)], partial(read_at_sha, str(repo)))
    assert not plan.needs_model, 'A source PR with an exact R100 rename must not translate'


@pytest.mark.parametrize("edited", [False, True])
def test_merged_rename_when_base_already_contains_rename(git_repo, edited):
    repo, git = git_repo
    seed(repo, git)
    git('mv', OLD, NEW)
    if edited:
        (repo / NEW).write_text('edited source\n')
        git('add', NEW)
    git('commit', '-m', 'merged rename')
    head = git('rev-parse', 'HEAD').decode().strip()
    frozen = freeze_snapshot(client(head, head, merged=True), 'o', 'r', 1)
    plan = build_plan(frozen, [ChangedFile(NEW, 'renamed', OLD, content_changed=edited)], partial(read_at_sha, str(repo)))
    assert not plan.no_work
    assert plan.needs_model is edited


@pytest.mark.parametrize('rows', [[], [{'filename': NEW, 'status': 'renamed'}], [{'filename': NEW, 'status': 'mystery'}]])
def test_api_empty_is_not_malformed(rows):
    api = SimpleNamespace(iter_pull_files=lambda *a: iter(rows))
    if not rows:
        assert list_changes(api, 'o', 'r', 1) == []
    else:
        with pytest.raises(PlanError):
            list_changes(api, 'o', 'r', 1)


def test_bilingual_add_modify_and_unrelated_removal_never_read_skipped_sources():
    sha = 'a' * 40
    frozen = freeze_snapshot(client(sha, sha), 'o', 'r', 1)
    changes = [ChangedFile('ydb/docs/ru/pair.md', 'added'),
               ChangedFile('ydb/docs/en/pair.md', 'modified'),
               ChangedFile('ydb/docs/en/gone.md', 'deleted')]
    def forbidden(*args):
        raise AssertionError('Bilingual skip and deletion must happen without source reads')
    plan = build_plan(frozen, changes, forbidden)
    assert len(plan.operations) == 1
    assert plan.operations[0].target_old_path == 'ydb/docs/ru/gone.md'
    assert not plan.needs_model
    assert not plan.no_work
