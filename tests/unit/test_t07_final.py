"""Independent final acceptance of REQUIREMENTS_RU §§3,5; offline critic only."""
import json
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from ydbdoc_review.document import RequestBudget, protect
from ydbdoc_review.parsing.markdown_parser import create_parser
from ydbdoc_review.quality import check, replace_validated_urls

OLD = 'https://docs.example.test/ru/topic'
NEW = 'https://docs.example.test/en/topic'
MAPPING = {OLD: NEW}


class Critic:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        assert kwargs['operation'] == 'critic'
        self.calls.append(messages)
        return SimpleNamespace(
            content='{"complete":true,"verdict":"correct","issues":[]}',
            finish_reason='stop',
        )


def verify(source, target, mapping=None, capacity=100000):
    critic = Critic()
    budget = RequestBudget(capacity, 200, lambda messages: len(str(messages)))
    result = check(source, target, path='en/final.md', candidate_sha='frozen-final',
                   target_lang='en', client=critic, choice=None, budget=budget,
                   validated_url_replacements=mapping)
    for issue in result.issues:
        issue.validate(source, target)
    assert all(budget.fits(messages) for messages in critic.calls)
    assert result.candidate_sha == 'frozen-final'
    return result, critic


def fm(value, field='description'):
    return f'---\n{field}: {json.dumps(value)}\nconfig: "{OLD}"\n---\n\nBody.\n'


def destinations(text):
    """Read effective addresses, independently of quality's replacement helper."""
    result = []

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            result.extend(value for name, value in attrs if name in {'href', 'src'})

    def walk(tokens):
        for token in tokens:
            if token.type == 'link_open':
                result.append(token.attrGet('href'))
            elif token.type in {'html_inline', 'html_block'}:
                Links().feed(token.content)
            walk(token.children or [])

    walk(create_parser().parse(text))
    return result


FORMS = [
    '[Read]({url} "Kept title")',
    '[Read][ref]\n\n[ref]: <{url}> "Kept title"\n',
    '<a class="kept" href="{url}">Read</a>',
    '<{url}>',
    'Read {url}',
]


@pytest.mark.parametrize('form', FORMS)
@pytest.mark.parametrize('field', [None, 'title', 'description'])
@pytest.mark.parametrize('suffix', ['/child', '?mode=other', '#other'])
def test_permission_does_not_extend_to_another_full_address(form, field, suffix):
    source, target = (form.format(url=url + suffix) for url in (OLD, NEW))
    if field:
        source, target = fm(source, field), fm(target, field)
    assert replace_validated_urls(source, MAPPING) == source
    result, _ = verify(source, target, MAPPING)
    assert not result.ok
    assert any(i.code == 'protected' for i in result.issues)


@pytest.mark.parametrize('field', ['title', 'description'])
def test_decoded_code_damage_not_authorized(field):
    source = fm(f'Run ``fetch("{OLD}")``', field)
    target = fm(f'Run ``fetch("{NEW}")``', field)
    assert any('fetch(' in raw for _, raw in protect(source).value_atoms)
    assert replace_validated_urls(source, MAPPING) == source
    result, _ = verify(source, target, MAPPING)
    assert not result.ok and any(i.code == 'protected' for i in result.issues)


@pytest.mark.parametrize('form', FORMS)
@pytest.mark.parametrize('field', ['title', 'description'])
def test_exact_link_and_decoded_code_same_address_remain_distinct(form, field):
    code = f'Execute ``fetch("{OLD}")``; '
    source = fm(code + form.format(url=OLD), field)
    target = fm(code + form.format(url=NEW), field)
    changed = replace_validated_urls(source, MAPPING)
    # Encoding may change; decoded executable bytes and unrelated config may not.
    assert protect(changed).front_matter[0].records[0].value == code + form.format(url=NEW)
    assert f'config: "{OLD}"\n' in changed
    result, _ = verify(source, target, MAPPING)
    assert result.ok, result.issues


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
def test_helper_preserves_all_non_destination_bytes(newline):
    source = (f'---\nconfig: "{OLD}" # keep\n---\n\n'
              f'> - [Read][ref]\n>\n>   [ref]: <{OLD}> "kept"\n\n'
              f'```python\nfetch("{OLD}") # unchanged\n```\n\n'
              f'<div data-url="{OLD}">\n<a href="{OLD}" title="kept">Read</a>\n</div>\n')
    target = source.replace(f'[ref]: <{OLD}>', f'[ref]: <{NEW}>').replace(
        f'href="{OLD}"', f'href="{NEW}"')
    source, target = (s.replace('\n', newline) for s in (source, target))
    assert replace_validated_urls(source, MAPPING) == target
    result, _ = verify(source, target, MAPPING)
    assert result.ok, result.issues


@pytest.mark.parametrize('field', [None, 'description'])
@pytest.mark.parametrize('form', FORMS[:3])
def test_explicit_query_fragment_mapping_changes_only_that_address(form, field):
    old, new = OLD + '?mode=read#part', NEW + '?mode=read#part'
    source = form.format(url=old) + '\n\n' + form.format(url=OLD)
    target = form.format(url=new) + '\n\n' + form.format(url=OLD)
    if field:
        source, target = fm(source), fm(target)
    result, _ = verify(source, target, {old: new})
    assert result.ok, result.issues


@pytest.mark.parametrize('field', [None, 'description'])
def test_html_entity_encoded_exact_query_is_accepted(field):
    old, new = OLD + '?a=1&b=2', NEW + '?a=1&b=2'
    source, target = (f'<a href="{u.replace("&", "&amp;")}">Read</a>' for u in (old, new))
    assert destinations(source) == [old]
    assert destinations(target) == [new]
    if field:
        source, target = fm(source), fm(target)
    result, _ = verify(source, target, {old: new})
    assert result.ok, result.issues


@pytest.mark.parametrize('field', [None, 'description'])
def test_escaped_markdown_destination_is_accepted(field):
    source = '[Read](' + OLD + r'\(intro\))'
    target = '[Read](' + NEW + r'\(intro\))'
    assert destinations(source) == [OLD + '(intro)']
    assert destinations(target) == [NEW + '(intro)']
    if field:
        source, target = fm(source), fm(target)
    result, _ = verify(source, target, {OLD + '(intro)': NEW + '(intro)'})
    assert result.ok, result.issues


@pytest.mark.parametrize('field', [None, 'description'])
@pytest.mark.parametrize('form', ['inline', 'reference', 'html'])
def test_helper_preserves_effective_exact_destination_when_escaping_is_required(form, field):
    if form == 'html':
        old, new = OLD + "?author=O'Reilly", NEW + "?author=O'Reilly"
        source = "<a href='" + old.replace("'", '&#39;') + "'>Read</a>"
    else:
        old, new = OLD + ')', NEW + ')'
        escaped = old.replace(')', r'\)')
        source = ('[Read](' + escaped + ')' if form == 'inline'
                  else '[Read][ref]\n\n[ref]: ' + escaped + '\n')
    assert destinations(source) == [old]
    if field:
        source = fm(source)
    updated = replace_validated_urls(source, {old: new})
    rendered = protect(updated).front_matter[0].records[0].value if field else updated
    assert destinations(rendered) == [new], updated


@pytest.mark.parametrize('field', [None, 'description'])
def test_check_does_not_accept_truncated_address_as_exact_mapping(field):
    old, new = OLD + ')', NEW + ')'
    source = '[Read](' + OLD + r'\))'
    damaged = '[Read](' + new + ')'
    assert destinations(source) == [old]
    assert destinations(damaged) == [NEW]  # A different, unvalidated address.
    if field:
        source, damaged = fm(source), fm(damaged)
    result, _ = verify(source, damaged, {old: new})
    assert not result.ok, result
    assert any(i.code == 'protected' for i in result.issues)


def test_mapping_is_simultaneous_in_decoded_fm_and_body():
    final = 'https://docs.example.test/archive'
    source = fm(f'<{OLD}> <{NEW}> `{OLD}`') + f'\n[Read]({OLD}) [Next]({NEW})\n'
    target = fm(f'<{NEW}> <{final}> `{OLD}`') + f'\n[Read]({NEW}) [Next]({final})\n'
    mapping = {OLD: NEW, NEW: final}
    assert replace_validated_urls(source, mapping) == target
    assert verify(source, target, mapping)[0].ok


def test_original_defect_alignment_with_translated_list_items():
    source_claims = [f'Условие {i} выполнено.' for i in range(20)]
    target_claims = [f'Condition {i} is satisfied.' for i in range(20)]
    source = ''.join('- ' + s + ' ' * (260 if i % 2 else 1) + '\n'
                     for i, s in enumerate(source_claims))
    target = ''.join('- ' + s + ' ' * (1 if i % 2 else 260) + '\n'
                     for i, s in enumerate(target_claims))
    result, critic = verify(source, target, capacity=3700)
    assert result.complete and len(critic.calls) > 1
    payloads = [json.loads(m[-1]['content']) for m in critic.calls]
    for side, raw in [('source', source), ('target', target)]:
        assert ''.join(p[side] for p in payloads) == raw
    for original, translation in zip(source_claims, target_claims, strict=True):
        assert any(original in p['source'] and translation in p['target'] for p in payloads)


def test_original_defects_multiline_code_unicode_location_and_cyrillic():
    text = 'Use ``literal\nliteral {% endcut %}\nend``.\n\nSafe\u2028 Ꙁ\n'
    assert any(c.type == 'code_inline' and '{% endcut %}' in c.content
               for t in create_parser().parse(text) for c in (t.children or []))
    result, _ = verify(text, text)
    assert result.complete and not result.ok
    assert not any(i.code == 'syntax' for i in result.issues)
    language = [i for i in result.issues if i.code == 'language']
    assert len(language) == 1
    assert language[0].target.start == language[0].target.end == 5
    warning, _ = verify('`Ꙁ`', '`Ꙁ`')
    assert warning.ok and warning.issues[0].severity == 'warning'
