"""Whole-tree language policy and bounded-loop integration, with real YFM."""
import pytest

from ydbdoc_review.links import check_links

from . import test_t09_recheck as fixtures

rig = fixtures.rig
P, RU = fixtures.P, fixtures.RU

pytestmark = pytest.mark.timeout(120)

OTHER = 'ydb/docs/en/other.md'


@pytest.mark.parametrize('syntax', ['[page]({})', '<a href="{}">page</a>', '[page][id]\n\n[id]: {}'])
@pytest.mark.parametrize('destination', ['b.md-extra', 'b', 'b.png'])
@pytest.mark.parametrize('english', [None, '', 'English page.'])
def test_full_tree_actual_links_require_english_target(rig, syntax, destination, english):
    href = '/ru/' + destination
    files = {P: 'Hello.', OTHER: syntax.format(href), 'ydb/docs/ru/' + destination: 'Page.'}
    if english is not None:
        files['ydb/docs/en/' + destination] = english
    tree = rig.commit(files)
    result = check_links(tree)
    assert result.complete and not result.ok
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.path == OTHER and href in issue.problem
    assert issue.target.start == 1
    assert ('replace with /en/' if english else 'specify a replacement') in issue.problem
    assert check_links(tree) == result


@pytest.mark.parametrize('body', [
    '![image](/ru/b.md-extra)', '<img src="/ru/b.md-extra">',
    '<video poster="/ru/b.md-extra"></video>',
    '`[page](/ru/missing.md-extra)`', '```md\n[page](/ru/missing.md-extra)\n```',
    '<!-- [page](/ru/missing.md-extra) -->',
])
def test_full_tree_assets_and_code_are_not_page_links(rig, body):
    tree = rig.commit({P: 'Hello.', OTHER: body, 'ydb/docs/ru/b.md-extra': 'Asset.'})
    assert check_links(tree).ok


def test_full_tree_ru_to_en_unusual_link_allowed(rig):
    tree = rig.commit({RU: '[English](/en/b.md-extra)', 'ydb/docs/en/b.md-extra': 'Page.'})
    assert check_links(tree).ok


@pytest.mark.parametrize('href', ['../ru/b.md-extra', 'https://example.invalid/docs/%72u/b'])
def test_shared_include_inherits_english_context_and_physical_resolution(rig, href):
    tree = rig.commit({P: 'Hello.', OTHER: '{% include [shared](../_includes/p.md) %}',
                       'ydb/docs/ru/other.md': '{% include [shared](../_includes/p.md) %}',
                       'ydb/docs/_includes/p.md': f'[page]({href})',
                       'ydb/docs/ru/b.md-extra': 'Page.'})
    result = check_links(tree)
    assert result.complete and len(result.issues) == 1
    assert result.issues[0].path == 'ydb/docs/_includes/p.md'
    assert href in result.issues[0].problem
    assert 'specify a replacement' in result.issues[0].problem


@pytest.mark.parametrize('body', ['[page](/ru/b.md-extra)', '<a href="/ru/b.md-extra">page</a>'])
def test_actual_yfm_full_tree_unselected_red_without_mutation(rig, body):
    initial, result = rig.run(extra={OTHER: body, 'ydb/docs/ru/b.md-extra': 'Page.'})
    assert result.status == 'RED'
    assert len(result.rounds) == 1  # §5.1: no repair changed this candidate
    assert all(r.build.ok_for(initial.sha) for r in result.rounds)
    assert all(len(r.links.issues) == 1 for r in result.rounds)
    assert all(not r.repairs for r in result.rounds)
    assert [op for op, _ in rig.calls] == ['critic']
    assert 'freeze' not in rig.events
    assert result.checked_sha == result.candidate.sha == initial.sha
    assert result.candidate.entries == initial.entries
    assert result.candidate.text(OTHER) == body
    assert result.candidate.read(RU) == initial.read(RU)


def test_actual_yfm_asset_does_not_propose_language_repair(rig):
    text = '![image](../ru/p.svg)'
    svg = '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    initial, result = rig.run(source=text, target=text, extra={
        'ydb/docs/ru/p.svg': svg, 'ydb/docs/en/p.svg': svg})
    assert result.status == 'GREEN', result.issues
    assert len(result.rounds) == 1 and not result.rounds[0].repairs
    assert result.rounds[0].build.ok_for(initial.sha)
    assert result.checked_sha == result.candidate.sha == initial.sha
    assert [op for op, _ in rig.calls] == ['critic']


def test_actual_yfm_ru_include_self_anchor_uses_rendered_page(rig):
    tree = rig.commit({P: '# Page\n\n{% include [part](../ru/fragment.md) %}',
                       'ydb/docs/ru/fragment.md': '## Part {#part}\n\n[self](#part)',
                       'ydb/docs/toc.yaml': 'title: Test\nitems:\n  - name: Page\n    href: en/a.md\n'})
    built = rig.build(tree)
    assert built.ok_for(tree.sha), built.log
    assert check_links(tree, build=built).ok
