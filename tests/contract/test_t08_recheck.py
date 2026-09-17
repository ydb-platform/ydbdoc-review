"""Independent T08 recheck: requirements §1, §3, §5, §6; no production edits."""
import pytest

from ydbdoc_review.build import BuildResult, automatic_ok, build_candidate
from ydbdoc_review.links import Candidate, check_links, confirmed_english_url, resolve

ROOT = 'ydb/docs/'


def snapshot(git_repo, files):
    repo, git = git_repo
    for name, value in files.items():
        path = repo / ROOT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if value is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(value.encode('utf-8') if isinstance(value, str) else value)
    git('add', '.')
    git('commit', '-m', 'isolated recheck fixture')
    return Candidate.open(repo, git('rev-parse', 'HEAD').decode().strip())


def successful(candidate, anchors=None):
    # Unit contract for links only. Real builder is independently exercised below.
    return BuildResult(candidate.sha, 'success', returncode=0, anchors=anchors or {})


@pytest.mark.parametrize('href', [
    'https://example.invalid/docs/r%75/page',
    'https://example.invalid/docs/%72%75/page?next=/en/page#en',
    '//example.invalid/docs/%72u',
    'https://example.invalid/%72u/page',
])
@pytest.mark.parametrize('syntax', ['[page]({})', '<a href="{}">page</a>'])
def test_russian_external_encoding_is_red_in_both_parsers(git_repo, href, syntax):
    candidate = snapshot(git_repo, {'en/a.md': syntax.format(href)})
    build = successful(candidate)
    result = check_links(candidate, build=build)
    assert not automatic_ok(candidate.sha, result, build)
    assert len(result.issues) == 1
    assert href in result.issues[0].problem
    assert 'replacement' in result.issues[0].problem
    with pytest.raises(ValueError, match='Unconfirmed'):
        confirmed_english_url(candidate, ROOT + 'en/a.md', href, build=build)


@pytest.mark.parametrize('href', [
    'https://ru.example.invalid/en/ruby?next=/%72u/page#ru',
    'https://example.invalid/en/rural',
    'https://example.invalid/docs/%2572u/page',
])
def test_no_false_language_matches_or_recursive_url_decoding(git_repo, href):
    candidate = snapshot(git_repo, {'en/a.md': f'[page]({href})'})
    assert check_links(candidate).ok
    assert confirmed_english_url(candidate, ROOT + 'en/a.md', href) == href


@pytest.mark.parametrize(('svg', 'fragment', 'valid'), [
    ('<svg xmlns="http://www.w3.org/2000/svg"><g id="café"/></svg>', 'caf%C3%A9', True),
    ('<svg><view id="a+b"/></svg>', 'a+b', True),
    ('<svg><view id="a+b"/></svg>', 'a%2Bb', True),
    ('<svg><view id="%6Fk"/></svg>', '%256Fk', True),
    ('<svg><view id="ok"/></svg>', '%256Fk', False),
    ('<svg><view id="a&amp;b"/></svg>', 'a%26b', True),
    ('<svg><view id="o&#107;"/></svg>', '%6Fk', True),
    ('<svg><view id="Case"/></svg>', 'case', False),
    ('<svg><!-- <g id="fake"/> --><text><![CDATA[<g id="fake"/>]]></text></svg>', 'fake', False),
    ('<svg><g data-id="fake"/><text>id="fake"</text></svg>', 'fake', False),
    ('<svg><view id="ok"/></svg>', 'missing', False),
    ('<svg><view id="ok"></svg>', 'ok', False),
])
def test_svg_ids_are_xml_ids_with_single_fragment_decoding(git_repo, svg, fragment, valid):
    candidate = snapshot(git_repo, {
        'en/a.md': f'![view](icon%20set.SVG?mode=1#{fragment})',
        'en/icon set.SVG': svg,
    })
    build = successful(candidate, {ROOT + 'en/icon set.SVG': {'fake', 'missing', 'case', '%6Fk'}})
    result = check_links(candidate, build=build)
    assert result.ok is valid, result.issues
    assert automatic_ok(candidate.sha, result, build) is valid
    if not valid:
        assert len(result.issues) == 1
        assert result.issues[0].path == ROOT + 'en/a.md'
        assert 'anchor' in result.issues[0].problem or 'Invalid SVG' in result.issues[0].problem


@pytest.mark.parametrize('in_candidate', [True, False])
def test_svg_ignores_index_worktree_new_head_and_fabricated_build_ids(git_repo, in_candidate):
    svg = '<svg><view id="{}"/></svg>'
    old = snapshot(git_repo, {
        'en/a.md': '![view](icon.svg#ok)',
        'en/icon.svg': svg.format('ok' if in_candidate else 'other'),
    })
    newer = snapshot(git_repo, {'en/icon.svg': svg.format('other' if in_candidate else 'ok')})
    path = old.repo / ROOT / 'en/icon.svg'
    path.write_text(svg.format('staged'))
    git_repo[1]('add', '.')
    path.write_text(svg.format('dirty'))
    for candidate, expected in [(old, in_candidate), (newer, not in_candidate)]:
        build = successful(candidate, {ROOT + 'en/icon.svg': {'ok', 'dirty', 'staged'}})
        assert check_links(candidate, build=build).ok is expected


@pytest.mark.parametrize('replacement', [None, ''])
def test_removed_or_empty_svg_cannot_fall_back_to_old_candidate(git_repo, replacement):
    old = snapshot(git_repo, {'en/a.md': '![view](icon.svg#ok)', 'en/icon.svg': '<svg id="ok"/>'})
    candidate = snapshot(git_repo, {'en/icon.svg': replacement})
    (candidate.repo / ROOT / 'en/icon.svg').write_text('<svg id="ok"/>')
    assert check_links(old, build=successful(old)).ok
    result = check_links(candidate, build=successful(candidate))
    assert not result.ok
    assert 'icon.svg' in result.issues[0].problem
    assert ('Missing' if replacement is None else 'Empty') in result.issues[0].problem


@pytest.mark.parametrize('state', ['none', 'pending', 'failure', 'wrong_sha', 'no_exit', 'bad_exit'])
def test_svg_valid_id_still_requires_successful_exact_sha(git_repo, state):
    candidate = snapshot(git_repo, {'en/a.md': '![view](icon.svg#ok)', 'en/icon.svg': '<svg id="ok"/>'})
    builds = {
        'none': None,
        'pending': BuildResult(candidate.sha, 'pending'),
        'failure': BuildResult(candidate.sha, 'failure', returncode=1),
        'wrong_sha': BuildResult('0' * 40, 'success', returncode=0),
        'no_exit': BuildResult(candidate.sha, 'success'),
        'bad_exit': BuildResult(candidate.sha, 'success', returncode=2),
    }
    result = check_links(candidate, build=builds[state])
    assert not result.ok and not result.complete
    assert 'successful build of this SHA' in result.issues[0].problem


def test_encoded_local_language_links_and_confirmed_proposal(git_repo):
    candidate = snapshot(git_repo, {
        'en/a.md': '[RU](/%72%75/b%20b.md?keep=%2F#%6Fk)',
        'ru/b b.md': '# B', 'en/b b.md': '# B',
        'ru/a.md': '[EN](/%65%6E/b%20b.md?keep=%2F#%6Fk)',
    })
    build = successful(candidate, {ROOT + 'en/b b.md': {'ok'}, ROOT + 'ru/b b.md': {'ok'}})
    assert not check_links(candidate, paths=[ROOT + 'en/a.md'], build=build).ok
    assert check_links(candidate, paths=[ROOT + 'ru/a.md'], build=build).ok
    assert confirmed_english_url(candidate, ROOT + 'en/a.md', '/%72%75/b%20b.md?keep=%2F#%6Fk', build=build) == '/en/b%20b.md?keep=%2F#%6Fk'
    original = '/%65%6E/b%20b.md?keep=%2F#%6Fk'
    assert confirmed_english_url(candidate, ROOT + 'ru/a.md', original, build=build) == original
    with pytest.raises(ValueError, match='anchor'):
        confirmed_english_url(candidate, ROOT + 'en/a.md', '/ru/b%20b.md#bad', build=build)


def test_real_yfm_svg_exact_candidate_and_ru_to_en(git_repo):
    candidate = snapshot(git_repo, {
        'toc.yaml': 'title: Recheck\nitems:\n  - name: English\n    href: en/a.md\n  - name: Russian\n    href: ru/a.md\n',
        'en/a.md': '# Page\n\n![view](icons.svg#%6Fk)\n\n## Explicit {#explicit}\n',
        'ru/a.md': '# Page\n\n[English](/en/a.md#explicit)\n',
        'en/icons.svg': '<svg xmlns="http://www.w3.org/2000/svg"><view id="ok" viewBox="0 0 10 10"/></svg>',
    })
    (candidate.repo / ROOT / 'en/icons.svg').write_text('<svg><view id="dirty"/></svg>')
    build = build_candidate(candidate)
    print('REAL RECHECK BUILD:', build.status, build.candidate_sha, build.log)
    assert build.ok_for(candidate.sha), build.log
    assert automatic_ok(candidate.sha, check_links(candidate, build=build), build)
    assert ROOT + 'en/icons.svg' not in build.anchors
    assert 'explicit' in build.anchors[ROOT + 'en/a.md']
    # Same snapshot SVG is also used for a confirmed replacement proposal.
    assert confirmed_english_url(candidate, ROOT + 'en/a.md', '/ru/icons.svg#%6Fk', build=build) == '/en/icons.svg#%6Fk'
    with pytest.raises(ValueError, match='anchor'):
        confirmed_english_url(candidate, ROOT + 'en/a.md', '/ru/icons.svg#dirty', build=build)


def test_url_decoding_preserves_literal_percent_and_rejects_escape():
    assert resolve(ROOT + 'en/a.md', 'a%2520b.svg#%256Fk').path == ROOT + 'en/a%20b.svg'
    assert resolve(ROOT + 'en/a.md', 'a%2520b.svg#%256Fk').fragment == '%6Fk'
    with pytest.raises(ValueError, match='escapes'):
        resolve(ROOT + 'en/a.md', '%2e%2e/%2e%2e/%2e%2e/outside.svg')


def test_real_yfm_success_does_not_hide_missing_svg_fragment(git_repo):
    candidate = snapshot(git_repo, {
        'toc.yaml': 'title: Negative\nitems:\n  - name: Page\n    href: en/a.md\n',
        'en/a.md': '# Page\n\n![view](icons.svg#missing)\n',
        'en/icons.svg': '<svg xmlns="http://www.w3.org/2000/svg"><view id="ok"/></svg>',
    })
    (candidate.repo / ROOT / 'en/icons.svg').write_text('<svg id="missing"/>')
    build = build_candidate(candidate)
    print('REAL NEGATIVE FRAGMENT BUILD:', build.status, build.candidate_sha, build.log)
    assert build.ok_for(candidate.sha), build.log
    result = check_links(candidate, build=build)
    assert not automatic_ok(candidate.sha, result, build)
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.path == ROOT + 'en/a.md'
    assert 'Missing anchor: icons.svg#missing' in issue.problem
    assert issue.target is not None
    issue.target.validate(candidate.text(issue.path))
