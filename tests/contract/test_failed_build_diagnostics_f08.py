"""F08: failed builds leave one dependent check, with exact-SHA details."""
from functools import lru_cache

import pytest

import ydbdoc_review.links as links_module
from tests.contract.test_links_build import ROOT, commit
from tests.contract.test_quality_loop_t09 import setup  # noqa: F401
from ydbdoc_review.build import BuildResult, automatic_ok
from ydbdoc_review.links import check_links


def test_thousands_of_unchecked_anchors_are_one_incomplete_check(git_repo, monkeypatch):
    # Keep real immutable Git blobs/SHA, but avoid spawning cat-file 8195 times
    # for the same target: this regression measures diagnostics, not Git I/O.
    monkeypatch.setattr(links_module, '_git', lru_cache(maxsize=None)(links_module._git))
    count = 8195
    tree = commit(git_repo, {
        'en/a.md': '\n\n'.join(f'[anchor {n}](b.md#id-{n})' for n in range(count))
                   + '\n\n[missing](gone.md#x)\n\n![missing asset](gone.png)\n\n[empty](empty.md#x)',
        'en/b.md': '# B',
        'en/empty.md': '',
    })
    build = BuildResult(tree.sha, 'failure', 'en/a.md:17: ERROR: invalid YFM', 1)
    result = check_links(tree, build=build)
    assert result.candidate_sha == tree.sha
    assert not result.complete and not result.ok
    assert not automatic_ok(tree.sha, result, build)
    assert len(result.issues) == 4
    grouped = [issue for issue in result.issues if issue.code == 'anchors_unchecked']
    assert len(grouped) == 1
    assert str(count) in grouped[0].problem and tree.sha in grouped[0].problem
    assert grouped[0].target is None and grouped[0].severity == 'error'
    assert 'not confirmed broken links' in grouped[0].problem
    proven = [issue for issue in result.issues if issue.code == 'links']
    assert len(proven) == 3
    assert any('Missing link target' in issue.problem for issue in proven)
    assert any('Missing asset target' in issue.problem for issue in proven)
    assert any('Empty link target' in issue.problem for issue in proven)
    assert len(result.unchecked_anchors) == count
    for n, detail in enumerate(result.unchecked_anchors):
        assert detail.path == ROOT + 'en/a.md'
        assert detail.target_path == ROOT + 'en/b.md'
        assert detail.rendered_page == ROOT + 'en/a.md'
        assert detail.href == f'b.md#id-{n}'
        assert detail.location.start == n * 2 + 1
        assert detail.location.quote.strip() == f'[anchor {n}](b.md#id-{n})'


@pytest.mark.parametrize('status,sha,code', [('failure', 'same', 1), ('pending', 'same', None),
                                           ('success', 'other', 0), ('success', 'same', None)])
def test_unavailable_or_wrong_sha_build_never_certifies_anchor(git_repo, status, sha, code):
    tree = commit(git_repo, {'en/a.md': '# A\n\n[one](#one)\n\n[two](#two)'})
    build = BuildResult(tree.sha if sha == 'same' else 'b' * 40, status, returncode=code,
                        anchors={ROOT + 'en/a.md': {'one', 'two'}})
    result = check_links(tree, build=build)
    assert not result.complete and not result.ok
    assert len(result.issues) == 1 and result.issues[0].code == 'anchors_unchecked'
    assert len(result.unchecked_anchors) == 2
    assert tree.sha in result.issues[0].problem


def test_absent_build_groups_anchors_and_success_retains_proven_missing_anchor(git_repo):
    tree = commit(git_repo, {'en/a.md': '# A\n\n[good](#good)\n\n[bad](#absent)'})
    incomplete = check_links(tree)
    assert len(incomplete.issues) == 1 and len(incomplete.unchecked_anchors) == 2
    build = BuildResult(tree.sha, 'success', returncode=0, anchors={ROOT + 'en/a.md': {'good'}})
    checked = check_links(tree, build=build)
    assert checked.complete and not checked.ok and not checked.unchecked_anchors
    assert len(checked.issues) == 1 and checked.issues[0].code == 'links'
    assert 'Missing anchor: #absent' in checked.issues[0].problem
    checked.issues[0].target.validate(tree.text(ROOT + 'en/a.md'))


def test_unchecked_include_fragment_does_not_hide_nested_errors(git_repo):
    tree = commit(git_repo, {
        'en/a.md': '{% include [part](part.md#section) %}',
        'en/part.md': '## Section\n\n[anchor](#section)\n\n![missing](missing.png)\n\n'
                      '{% include [cycle](a.md#section) %}',
    })
    result = check_links(tree, paths=[ROOT + 'en/a.md'],
                         build=BuildResult(tree.sha, 'failure', 'YFM failed', 1))
    assert not result.complete and not result.ok
    assert len([issue for issue in result.issues if issue.code == 'anchors_unchecked']) == 1
    assert any('Missing asset' in issue.problem for issue in result.issues)
    assert any('Include cycle' in issue.problem for issue in result.issues)
    assert len(result.unchecked_anchors) == 3
    assert all(detail.rendered_page == ROOT + 'en/a.md' for detail in result.unchecked_anchors)


@pytest.mark.parametrize('preceding', ['Progress\n' * 400, 'Progress: ' + 'p' * 2500 + '\n'])
def test_build_diagnostic_preserves_first_failure_place_and_full_log(preceding):
    primary = 'en/core/a.md:17: ERROR: unclosed YFM container'
    log = 'Preparing pages\n' + preceding + primary + '\n' + ('Dependent failure\n' * 1000)
    result = BuildResult('a' * 40, 'failure', log, 1)
    issue, = result.issues_for('a' * 40)
    assert issue.code == 'build'
    assert primary in issue.problem
    assert 'a' * 40 in issue.problem
    assert 'No baseline comparison' in issue.problem
    assert issue.target is None  # never invent source coordinates from CLI output
    assert len(issue.problem) < 2500
    assert result.log == log


def test_grouped_dependent_check_keeps_loop_red_on_checked_sha(setup):  # noqa: F811 -- imported pytest fixture
    run, *_ = setup
    _, result = run(
        extra={ROOT + 'en/other.md': '# Heading\n\n[one](#one)\n\n[two](#two)'},
        build=lambda candidate: BuildResult(candidate.sha, 'failure',
                                             'en/other.md:1: ERROR: invalid YFM', 1))
    assert result.status == 'RED'
    assert result.checked_sha == result.candidate.sha
    assert result.issues[0].code == 'build'
    grouped = [issue for issue in result.issues if issue.code == 'anchors_unchecked']
    assert len(grouped) == 1 and result.checked_sha in grouped[0].problem
    assert not any('Anchor not verified' in issue.problem for issue in result.issues)
    assert all(trace.links.candidate_sha == trace.candidate_sha for trace in result.rounds)


@pytest.mark.parametrize('notice', ['INFO: error reporting enabled', '[WARN] missing optional config',
                                    'INFO: ERROR reporting enabled'])
@pytest.mark.parametrize('level', ['ERROR', 'ERR', 'FATAL', '\x1b[31mERROR\x1b[0m'])
def test_primary_severity_beats_informational_keywords_and_long_prefix(notice, level):
    primary = f'en/core/source.md:17: {level}: unclosed YFM container'
    log = notice + '\n' + 'Progress\n' * 400 + 'context ' * 400 + primary + '\nsecondary'
    result = BuildResult('a' * 40, 'failure', log, 1)
    issue, = result.issues_for('a' * 40)
    assert 'en/core/source.md:17:' in issue.problem
    assert 'unclosed YFM container' in issue.problem
    assert notice not in issue.problem
    assert '\x1b' not in issue.problem
    assert len(issue.problem) < 2500
    assert result.log == log


def test_unstructured_failure_fallback_skips_informational_keywords():
    log = 'INFO: error reporting enabled\n' + 'Progress\n' * 400 + 'YFM timed out after 600s'
    issue, = BuildResult('a' * 40, 'failure', log, 1).issues_for('a' * 40)
    assert 'YFM timed out after 600s' in issue.problem
