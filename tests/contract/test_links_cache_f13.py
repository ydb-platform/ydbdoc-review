"""Real Git targets: bounded per-check reads preserve every reference and SHA."""
from collections import Counter

import pytest

from tests.contract.test_links_build import ROOT, commit
from ydbdoc_review.build import BuildResult
from ydbdoc_review.links import Candidate, check_links


@pytest.fixture
def reads(monkeypatch):
    counts = Counter()
    original = Candidate.read

    def observed(candidate, path):
        counts[candidate.sha, path] += 1
        return original(candidate, path)

    monkeypatch.setattr(Candidate, 'read', observed)
    return counts


@pytest.mark.parametrize('success', [True, False])
def test_repeated_target_read_once_but_all_references_checked(git_repo, reads, success):
    count = 257
    tree = commit(git_repo, {'en/a.md': '\n\n'.join(
        f'[link{i}](b.md#absent)' for i in range(count)), 'en/b.md': '# Target\n'})
    build = BuildResult(tree.sha, 'success' if success else 'failure', returncode=0 if success else 1)
    result = check_links(tree, paths=[ROOT + 'en/a.md'], build=build)
    assert reads[tree.sha, ROOT + 'en/b.md'] == 1
    assert not result.ok
    if success:
        assert len(result.issues) == count
        assert all('Missing anchor' in issue.problem for issue in result.issues)
        assert len({issue.target.start for issue in result.issues}) == count
    else:
        assert len(result.unchecked_anchors) == count
        assert len(result.issues) == 1 and result.issues[0].code == 'anchors_unchecked'
    again = check_links(tree, paths=[ROOT + 'en/a.md'], build=build)
    assert again == result
    assert reads[tree.sha, ROOT + 'en/b.md'] == 2  # cache belongs to one check


def test_new_sha_reads_svg_again_and_include_context_is_preserved(git_repo, reads):
    tree = commit(git_repo, {'en/a.md': '[ok](x.svg#ok)\n\n[bad](x.svg#bad)\n\n'
        '{% include [part](part.md) %}\n\n{% include [part](part.md) %}',
        'en/part.md': '[included](x.svg#ok)', 'en/x.svg': '<svg><g id="ok"/></svg>'})
    build = BuildResult(tree.sha, 'success', returncode=0)
    result = check_links(tree, paths=[ROOT + 'en/a.md'], build=build)
    assert reads[tree.sha, ROOT + 'en/x.svg'] == 1
    assert len(result.issues) == 1 and 'x.svg#bad' in result.issues[0].problem
    new = commit(git_repo, {'en/x.svg': '<svg><g id="bad"/></svg>'})
    changed = check_links(new, paths=[ROOT + 'en/a.md'],
                          build=BuildResult(new.sha, 'success', returncode=0))
    assert reads[new.sha, ROOT + 'en/x.svg'] == 1
    assert len(changed.issues) == 2
    assert {issue.path for issue in changed.issues} == {ROOT + 'en/a.md', ROOT + 'en/part.md'}
    assert all('x.svg#ok' in issue.problem for issue in changed.issues)
    assert check_links(tree, paths=[ROOT + 'en/a.md'], build=build) == result


@pytest.mark.parametrize('count,size', [(130, 1), (20, 60 * 1024)])
def test_entry_and_total_byte_bounds_evict_old_targets(git_repo, reads, count, size):
    files = {f'en/asset{i}.bin': b'x' * size for i in range(count)}
    files['en/a.md'] = '\n\n'.join(f'[asset](asset{i}.bin)' for i in range(count)) + (
        f'\n\n[first again](asset0.bin)\n\n[last again](asset{count - 1}.bin)')
    tree = commit(git_repo, files)
    result = check_links(tree, paths=[ROOT + 'en/a.md'])
    assert result.ok
    assert reads[tree.sha, ROOT + 'en/asset0.bin'] == 2
    assert reads[tree.sha, ROOT + f'en/asset{count - 1}.bin'] == 1


def test_large_targets_bypass_cache_and_read_errors_are_not_cached(git_repo, reads, monkeypatch):
    tree = commit(git_repo, {'en/a.md': '[large](large.bin)\n\n[large](large.bin)\n\n'
        '[error](error.bin)\n\n[error](error.bin)', 'en/large.bin': b'x' * (64 * 1024 + 1),
        'en/error.bin': b'x'})
    original = Candidate.read
    errors = []

    def failing(candidate, path):
        if path == ROOT + 'en/error.bin':
            errors.append(path)
            raise OSError('independent read failure')
        return original(candidate, path)

    monkeypatch.setattr(Candidate, 'read', failing)
    result = check_links(tree, paths=[ROOT + 'en/a.md'])
    assert reads[tree.sha, ROOT + 'en/large.bin'] == 2
    assert len(errors) == len(result.issues) == 2
    assert all('independent read failure' in issue.problem for issue in result.issues)
