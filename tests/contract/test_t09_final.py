"""Independent final T09 acceptance; only external HTTP is mocked by fixtures."""
import json
from html.parser import HTMLParser
from urllib.parse import unquote

import pytest
import yaml

from tests.contract import test_t09_recheck as loop_fixtures
from tests.contract import test_t10_independent as runner_fixtures
from ydbdoc_review.links import check_links, references
from ydbdoc_review.parsing.markdown_parser import create_parser
from ydbdoc_review.quality_loop import QualityLoopInterrupted

rig = loop_fixtures.rig
runner_rig = runner_fixtures.rig
ROOT = 'ydb/docs/'
pytestmark = pytest.mark.timeout(120)


@pytest.mark.parametrize('lang', ['en', 'ru'])
@pytest.mark.parametrize('syntax', ['[page]({})', '<a href="{}">page</a>'])
@pytest.mark.parametrize('owner', ['body', 'title', 'description'])
def test_nested_shared_include_language_and_single_issue(rig, lang, syntax, owner):
    other = 'ru' if lang == 'en' else 'en'
    href = f'../{other}/b.md-extra'
    fragment = syntax.format(href)
    if owner != 'body':
        fragment = f'---\n{owner}: {json.dumps(fragment)}\n---\n'
    tree = rig.commit({
        ROOT + f'{lang}/unselected.md': '{% include [outer](../_includes/outer.md) %}',
        ROOT + '_includes/outer.md': '{% include [inner](inner.md) %}',
        ROOT + '_includes/inner.md': fragment,
        ROOT + f'{other}/b.md-extra': 'Page.',
        ROOT + 'en/a.md': 'Selected page.',
    })
    result = check_links(tree)
    assert result.complete
    if lang == 'ru':
        assert result.ok and not result.issues
    else:
        assert not result.ok and len(result.issues) == 1
        issue = result.issues[0]
        assert issue.path == ROOT + '_includes/inner.md'
        assert href in issue.problem and 'specify a replacement' in issue.problem
        issue.validate(fragment, fragment)
        assert (issue.target is None) == (owner != 'body')
    assert check_links(tree) == result


@pytest.mark.parametrize('syntax', ['[page]({})', '<a href="{}">page</a>'])
def test_actual_loop_nested_unselected_include_no_duplicate_or_extra_repair(rig, syntax):
    fragment = syntax.format('../ru/b.md-extra')
    before, result = rig.run(extra={
        ROOT + 'en/unselected.md': '{% include [outer](../_includes/outer.md) %}',
        ROOT + '_includes/outer.md': '{% include [inner](inner.md) %}',
        ROOT + '_includes/inner.md': fragment,
        ROOT + 'ru/b.md-extra': 'Page.',
    })
    assert result.status == 'RED', result.issues
    assert result.checked_sha == result.candidate.sha == before.sha
    assert result.candidate.entries == before.entries
    assert len(result.rounds) == 1  # §5.1: no repair changed this candidate
    assert [op for op, _ in rig.calls] == ['critic']
    assert 'freeze' not in rig.events
    for trace in result.rounds:
        assert trace.build.ok_for(before.sha), trace.build.log
        assert trace.links.complete and len(trace.links.issues) == 1
        assert not trace.repairs
        language = [i for i in trace.issues if 'English page links to Russian page' in i.problem]
        assert len(language) == 1
        language[0].validate(fragment, fragment)


@pytest.mark.parametrize('key', ['title', 'description'])
def test_decoded_folded_references_ignore_code_config_and_keep_body_coordinates(key):
    text = (
        f'---\n{key}: >-\n  [Read\n  more](/ru/b.md-extra)\n'
        '  `[hidden](/ru/code.md)`\n'
        '  <!-- <a href="/ru/comment.md">hidden</a> -->\n'
        'config: "[opaque](/ru/config.md)"\n---\n\n'
        '```md\n[hidden](/ru/fence.md)\n```\n\n[body](/en/body.md)\n'
    )
    refs = references(text)
    assert [(r.href, r.kind) for r in refs] == [
        ('/ru/b.md-extra', 'link'), ('/en/body.md', 'link')]
    assert refs[0].location is None
    assert refs[1].location is not None
    refs[1].location.validate(text)
    tree_refs = references(yaml.safe_load(text.split('---', 2)[1])[key])
    assert [r.href for r in tree_refs] == ['/ru/b.md-extra']


class Hrefs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.urls.append(dict(attrs)['href'])


def effective_urls(value):
    """Independent parser oracle, not links.references or URL replacement helper."""
    urls = []
    pending = list(reversed(create_parser().parse(value)))
    while pending:
        token = pending.pop()
        pending.extend(reversed(token.children or []))
        if token.type == 'link_open':
            urls.append(unquote(token.attrGet('href')))
        elif token.type in {'html_inline', 'html_block'}:
            parser = Hrefs()
            parser.feed(token.content)
            urls.extend(unquote(url) for url in parser.urls)
    return urls


@pytest.mark.parametrize('key', ['title', 'description'])
def test_actual_runner_mixed_decoded_exact_urls_and_opaque_atoms(runner_rig, key):
    state, run, put, *_ = runner_rig
    old = '/ru/topic).md?x=1&y=2'
    value = (r'[MD](/ru/topic\).md?x=1&amp;y=2) '
             '<a href="/ru/topic).md?x=1&amp;y=2">HTML</a> '
             f'`{old}`')
    assert effective_urls(value) == [old, old]
    config = 'config: "[opaque](/ru/config.md)" # unchanged\n'
    source = f'---\n{key}: {json.dumps(value)}\n{config}---\n# Article\n'
    put({'ru/a.md': source, 'ru/topic).md': '# Topic\n', 'en/topic).md': '# Topic\n'})
    result = run()
    assert result.candidate is not None, result.errors
    target = result.candidate.text(ROOT + 'en/a.md')
    decoded = yaml.safe_load(target.split('---', 2)[1])[key]
    assert effective_urls(decoded) == ['/en/topic).md?x=1&y=2'] * 2, decoded
    assert f'`{old}`' in decoded
    assert config in target
    assert result.candidate.text(ROOT + 'ru/a.md') == source
    assert result.status == 'GREEN', (result.errors, result.issues)
    assert result.checked_sha == result.result_sha == result.candidate.sha
    assert result.publication.draft is False
    assert [op for op, _ in state['calls']] == ['translation', 'critic', 'repair', 'critic']


def test_interruption_after_real_build_keeps_repaired_candidate_and_trace(rig):
    built = []

    def interrupt_second_build(candidate):
        result = rig.build(candidate)
        assert result.ok_for(candidate.sha), result.log
        built.append(candidate)
        if len(built) == 2:
            raise KeyboardInterrupt('independent final acceptance')
        return result

    with pytest.raises(QualityLoopInterrupted) as caught:
        rig.run(target='Ошибка', builder=interrupt_second_build)
    result = caught.value.result
    assert result.status == 'RED'
    assert result.candidate.sha == built[1].sha != built[0].sha
    assert result.checked_sha == built[0].sha
    assert result.candidate.text(ROOT + 'en/a.md') == 'Hello.'
    assert result.candidate.read(ROOT + 'ru/a.md') == built[0].read(ROOT + 'ru/a.md')
    assert len(result.rounds) == 2
    assert result.rounds[0].repairs[0].complete
    assert not result.rounds[1].repairs
    assert [op for op, _ in rig.calls] == ['critic', 'repair']
    assert rig.events.count('freeze') == 1
