"""Independent fix3 acceptance: mixed destinations and opaque neighbouring bytes."""
import json
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
import yaml

from ydbdoc_review.document import RequestBudget
from ydbdoc_review.parsing.markdown_parser import create_parser
from ydbdoc_review.quality import check, replace_validated_urls

OLD = 'https://accept.example/ru/topic'
NEW = 'https://accept.example/en/topic'


def hrefs(text):
    found = []

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            found.extend(v for k, v in attrs if k in {'href', 'src'})

    def walk(tokens):
        for token in tokens:
            if token.type == 'link_open':
                found.append(token.attrGet('href'))
            elif token.type == 'image':
                found.append(token.attrGet('src'))
            elif token.type in {'html_inline', 'html_block'}:
                Links().feed(token.content)
            walk(token.children or [])

    walk(create_parser().parse(text))
    return found


def wrap(value, style, newline):
    if style == 'body':
        return value.replace('\n', newline)
    if style == 'double':
        scalar = json.dumps(value)
    elif style == 'single':
        scalar = "'" + value.replace("'", "''").replace('\n', '\n\n  ') + "'"
    else:
        scalar = '|-\n' + ''.join('  ' + line + '\n' for line in value.splitlines())
    return (f'---\ndescription: {scalar}\nconfig: "{OLD}" # untouched\n'
            'flag: true\n---\n\nBody.\n').replace('\n', newline)


def decoded(text, style):
    if style == 'body':
        return text
    return yaml.safe_load(text.split('---', 2)[1])['description']


class Critic:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        assert kwargs['operation'] == 'critic'
        self.calls.append(messages)
        return SimpleNamespace(content='{"complete":true,"verdict":"correct","issues":[]}',
                               finish_reason='stop')


def verify(source, target, mapping):
    critic = Critic()
    before = (source.encode(), target.encode(), dict(mapping))
    result = check(source, target, path='en/accept.md', candidate_sha='accept-frozen',
                   target_lang='en', client=critic, choice=None,
                   budget=RequestBudget(100000, 200, lambda m: len(str(m))),
                   validated_url_replacements=mapping)
    assert (source.encode(), target.encode(), dict(mapping)) == before
    assert result.candidate_sha == 'accept-frozen'
    assert critic.calls
    for issue in result.issues:
        issue.validate(source, target)
    return result


@pytest.mark.parametrize('style', ['body', 'double', 'single', 'literal'])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('suffix', [')?a=1&literal=&amp;#end', '/%29?q=%22#tail'])
def test_mixed_contexts_exact_destinations_opaque_bytes_and_no_cascade(style, newline, suffix):
    # Shared reference used twice plus MD, autolink and quoted HTML exercise
    # overlapping parser provenance and repeated semantic destinations.
    body = (f'[Inline]({OLD})\n\n[One][r] [Two][r]\n\n[r]: <{OLD}> "kept"\n\n'
            f'<{OLD}>\n\n<a data-url="{OLD}" href=\'{OLD}\'>HTML</a>\n\n'
            f'`fetch("{OLD}")`\n\n[Boundary]({OLD}/child)\n')
    source = wrap(body, style, newline)
    assert hrefs(decoded(source, style)) == [OLD] * 5 + [OLD + '/child']
    mapping = {OLD: NEW + suffix, NEW + suffix: 'https://accept.example/cascade'}
    updated = replace_validated_urls(source, mapping)
    rendered = decoded(updated, style)
    assert hrefs(rendered) == [NEW + suffix] * 5 + [OLD + '/child']
    for opaque in (f'`fetch("{OLD}")`', f'data-url="{OLD}"', '"kept"'):
        assert opaque.encode() in rendered.encode()
    if style != 'body':
        assert f'config: "{OLD}" # untouched{newline}flag: true{newline}'.encode() in updated.encode()
    assert verify(source, updated, mapping).ok
    damaged = updated.replace('fetch(', 'destroy(')
    result = verify(source, damaged, mapping)
    assert not result.ok and any(i.code == 'protected' for i in result.issues)


@pytest.mark.parametrize('style', ['body', 'double', 'single', 'literal'])
@pytest.mark.parametrize('form', ['[Read]({})', '[Read][r]\n\n[r]: {}\n', '<{}>', '<a href="{}">Read</a>'])
def test_escaped_mapping_cannot_authorize_truncated_query(style, form):
    old = OLD + '?a=1&literal=&amp;'
    new = NEW + '?a=1&literal=&amp;'
    source = wrap(form.format(OLD), style, '\n')
    source = replace_validated_urls(source, {OLD: old})
    valid = replace_validated_urls(source, {old: new})
    assert hrefs(decoded(source, style)) == [old]
    assert hrefs(decoded(valid, style)) == [new]
    assert verify(source, valid, {old: new}).ok
    damaged = replace_validated_urls(valid, {new: NEW + '?a=1'})
    assert hrefs(decoded(damaged, style)) == [NEW + '?a=1']
    result = verify(source, damaged, {old: new})
    assert not result.ok and any(i.code == 'protected' for i in result.issues)
