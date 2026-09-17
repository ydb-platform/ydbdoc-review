"""Exact URL authorization, with real parser ownership and offline critic."""
import json
from types import SimpleNamespace

import pytest

from ydbdoc_review.document import RequestBudget
from ydbdoc_review.quality import check, replace_validated_urls

OLD = 'https://example.test/ru/page'
NEW = 'https://example.test/en/page'
MAPPING = {OLD: NEW}


class Critic:
    def chat(self, messages, **kwargs):
        assert kwargs['operation'] == 'critic'
        return SimpleNamespace(content=json.dumps({
            'complete': True, 'verdict': 'correct', 'issues': [],
        }), finish_reason='stop')


def verify(source, target):
    result = check(source, target, path='en/a.md', target_lang='en',
                   candidate_sha='fixed', client=Critic(), choice=None,
                   budget=RequestBudget(100000, 1000, lambda m: len(str(m))),
                   validated_url_replacements=MAPPING)
    for issue in result.issues:
        issue.validate(source, target)
    return result


def fm(value, key='description'):
    return f'---\n{key}: {json.dumps(value)}\n---\n\nBody.\n'


FORMS = [
    '[Guide]({url})',
    '[Guide](<{url}> "Title")',
    '![Image]({url})',
    '<{url}>',
    'Visit {url}',
    '[Guide][id]\n\n[id]: {url} "Title"\n',
    '[Guide][id]\n\n[id]:\n  <{url}>\n',
    '<a href="{url}" title="Title">Guide</a>',
    '<div>\n<a href=\'{url}\'>Guide</a>\n</div>\n',
    '<img src={url}>',
    '<video poster="{url}"></video>',
]


@pytest.mark.parametrize('form', FORMS)
@pytest.mark.parametrize('suffix', ['', '/child', '?different=1', '#other'])
@pytest.mark.parametrize('decoded', [False, True])
def test_complete_destination_only(form, suffix, decoded):
    source, target = (form.format(url=url + suffix) for url in (OLD, NEW))
    if decoded:
        source, target = fm(source), fm(target)
    expected = source if suffix else target
    assert replace_validated_urls(source, MAPPING) == expected
    result = verify(source, target)
    assert result.ok == (not suffix), result.issues
    if suffix:
        assert any(i.code == 'protected' for i in result.issues)


@pytest.mark.parametrize('form', [FORMS[0], FORMS[3], FORMS[4], FORMS[7]])
@pytest.mark.parametrize('key', ['title', 'description'])
def test_decoded_fm_exact_link_with_unchanged_code(form, key):
    code = f'Example `fetch("{OLD}")` '
    source = fm(code + form.format(url=OLD), key)
    target = fm(code + form.format(url=NEW), key)
    assert verify(source, target).ok
    changed_code = fm(code.replace(OLD, NEW) + form.format(url=NEW), key)
    assert not verify(source, changed_code).ok


@pytest.mark.parametrize('opaque', [
    '`{url}`',
    '``fetch("{url}")``',
    '```json\n{{"url": "{url}"}}\n```\n',
    '    fetch("{url}")\n',
    '<!-- <a href="{url}"> -->',
    '<a title="{url}" data-url="{url}">Guide</a>',
    '[Guide][id]\n\n[id]: elsewhere "{url}"\n',
    '---\nconfig: "{url}"\n---\n',
])
def test_link_permission_never_changes_code_or_config(opaque):
    kept = opaque.format(url=OLD)
    source = kept + f'\n\n[Guide]({OLD})\n'
    target = kept + f'\n\n[Guide]({NEW})\n'
    assert replace_validated_urls(source, MAPPING) == target
    assert verify(source, target).ok
    damaged = opaque.format(url=NEW) + f'\n\n[Guide]({NEW})\n'
    assert not verify(source, damaged).ok


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
def test_offsets_nested_reference_html_and_code(newline):
    source = (f'> [Guide][id]\n>\n> [id]: <{OLD}>\n\n'
              f'- [Guide]({OLD}) and `{OLD}`\n\n'
              f'<div>\n<a href="{OLD}" title="{OLD}">Guide</a>\n</div>\n')
    target = (f'> [Guide][id]\n>\n> [id]: <{NEW}>\n\n'
              f'- [Guide]({NEW}) and `{OLD}`\n\n'
              f'<div>\n<a href="{NEW}" title="{OLD}">Guide</a>\n</div>\n')
    source, target = (t.replace('\n', newline) for t in (source, target))
    assert replace_validated_urls(source, MAPPING) == target
    assert verify(source, target).ok


def test_replacements_are_simultaneous_not_cascading():
    source = f'[One]({OLD}) [Two]({NEW})'
    target = f'[One]({NEW}) [Two](https://example.test/final)'
    assert replace_validated_urls(source, {OLD: NEW, NEW: 'https://example.test/final'}) == target


def test_decoded_yaml_escaped_url_and_config_same_address():
    source = fm(f'Visit {OLD} and `{OLD}`').replace('https:', r'\u0068ttps:', 1)
    target = fm(f'Visit {NEW} and `{OLD}`')
    assert verify(source, target).ok
