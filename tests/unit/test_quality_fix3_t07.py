"""Context serialization must preserve effective, fully validated destinations."""
import json
from html.parser import HTMLParser

import pytest

from ydbdoc_review.document import protect
from ydbdoc_review.parsing.markdown_parser import create_parser
from ydbdoc_review.quality import deterministic_checks, replace_validated_urls

OLD = 'https://example.test/ru/topic'
NEW = 'https://example.test/en/topic'
FORMS = [
    '[Read]({url} "kept")',
    '[Read](<{url}> "kept")',
    '![Image]({url})',
    '[Read][ref]\n\n[ref]: {url} "kept"\n',
    '[Read][ref]\n\n[ref]: <{url}> "kept"\n',
    '<{url}>',
    '<a href="{url}" data-kept="yes">Read</a>',
    "<a href='{url}' data-kept='yes'>Read</a>",
    '<img src={url}>',
]


def hrefs(text):
    values = []

    class HTMLLinks(HTMLParser):
        def handle_starttag(self, tag, attrs):
            values.extend(v for k, v in attrs if k in {'href', 'src', 'poster'})

    def walk(tokens):
        for token in tokens:
            if token.type == 'link_open':
                values.append(token.attrGet('href'))
            if token.type == 'image':
                values.append(token.attrGet('src'))
            if token.type in {'html_inline', 'html_block'}:
                HTMLLinks().feed(token.content)
            walk(token.children or [])

    walk(create_parser().parse(text))
    return values


def wrap(text, field):
    if field is None:
        return text
    return f'---\n{field}: {json.dumps(text)}\nconfig: "{OLD}"\n---\n\nBody.\n'


def decoded(text, field):
    return protect(text).front_matter[0].records[0].value if field else text


@pytest.mark.parametrize('field', [None, 'title', 'description'])
@pytest.mark.parametrize('form', FORMS)
@pytest.mark.parametrize('suffix', [')', '(intro)', '?a=1&b=2', "?author=O'Reilly",
                                     '?q="yes"', '?literal=&amp;', '/%20?q=%22#part'])
def test_serialization_round_trip_and_protection(form, suffix, field):
    target = NEW + suffix
    # Quotes must be URI encoded in Markdown's effective href, but HTML attrs
    # can contain the actual character. Test the exact parser-level contract.
    if not form.startswith(('<a ', '<img ')):
        target = target.replace('"', '%22')
    original = form.format(url=OLD)
    opaque = f'\n\n`{OLD}`\n\n[Other]({OLD}/child)\n'
    source = wrap(original + opaque, field)
    updated = replace_validated_urls(source, {OLD: target})
    rendered = decoded(updated, field)
    assert hrefs(rendered) == [target, OLD + '/child']
    assert rendered.endswith(opaque)
    assert replace_validated_urls(updated, {OLD: target}) == updated
    assert not deterministic_checks(source, updated, path='test.md', target_lang='en',
                                    validated_url_replacements={OLD: target})
    if field:
        assert f'config: "{OLD}"\n' in updated
    damaged = updated.replace(f'`{OLD}`', f'`{NEW}`') if not field else wrap(
        rendered.replace(f'`{OLD}`', f'`{NEW}`'), field)
    assert any(i.code == 'protected' for i in deterministic_checks(
        source, damaged, path='test.md', target_lang='en',
        validated_url_replacements={OLD: target}))


@pytest.mark.parametrize('field', [None, 'title', 'description'])
@pytest.mark.parametrize('source_link,target_link,old,new', [
    ('[Read](' + OLD + r'\(x\))', '[Read](' + NEW + '(x))', OLD + '(x)', NEW + '(x)'),
    ('[Read](' + OLD + '&#41;)', '[Read](' + NEW + r'\))', OLD + ')', NEW + ')'),
    ('<a href="' + OLD + '?a=1&amp;b=2">Read</a>',
     '<a href="' + NEW + '?a=1&#38;b=2">Read</a>', OLD + '?a=1&b=2', NEW + '?a=1&b=2'),
])
def test_equivalent_destination_spellings(field, source_link, target_link, old, new):
    assert hrefs(source_link) == [old]
    assert hrefs(target_link) == [new]
    assert not deterministic_checks(wrap(source_link, field), wrap(target_link, field),
                                    path='test.md', target_lang='en',
                                    validated_url_replacements={old: new})
