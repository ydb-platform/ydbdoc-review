"""Independent T07 recheck against REQUIREMENTS_RU §§3,5; no live services."""
import json
import re
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from ydbdoc_review.document import RequestBudget, protect
from ydbdoc_review.model import ModelError
from ydbdoc_review.parsing.markdown_parser import create_parser
from ydbdoc_review.quality import check

PATH = 'en/recheck.md'
GOOD = {'complete': True, 'verdict': 'correct', 'issues': []}


class RecordingCritic:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def chat(self, messages, **kwargs):
        assert kwargs['operation'] == 'critic'
        self.calls.append((messages, json.loads(messages[-1]['content'])))
        if len(self.calls) == self.fail_at:
            raise ModelError('offline simulated failure')
        return SimpleNamespace(content=json.dumps(GOOD), finish_reason='stop')


def run(source, target, capacity=100000, client=None, **kwargs):
    client = client or RecordingCritic()
    budget = RequestBudget(capacity, 200, lambda messages: len(str(messages)))
    result = check(source, target, path=PATH, candidate_sha='fixed-candidate',
                   target_lang='en', client=client, choice=None, budget=budget, **kwargs)
    assert all(budget.fits(messages) for messages, _ in client.calls)
    assert len(client.calls) == len(result.parts)
    for issue in result.issues:
        issue.validate(source, target)
    return result, client


def coverage(source, target, result, client):
    for side, text in [('source', source), ('target', target)]:
        cursor = 0
        for part, (_, payload) in zip(result.parts, client.calls, strict=True):
            start, end = getattr(part, side + '_start'), getattr(part, side + '_end')
            assert start == cursor
            assert payload[side] == text[start:end]
            assert payload[side + '_start_line'] == len(re.findall(r'\r\n|\r|\n', text[:start])) + 1
            cursor = end
        assert cursor == len(text)
        assert ''.join(p[side] for _, p in client.calls) == text


@pytest.mark.parametrize('layout', ['paragraph', 'heading', 'table', 'code', 'yfm'])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_long_counterpart_delivery_and_raw_coverage(layout, newline):
    originals, translations = [], []
    for i in range(18):
        # The claim IDs, semantics and order correspond; physical lengths do not.
        source = f'Утверждение {i}: значение сохранено'
        target = f'Claim {i}: value is retained'
        originals.append(source)
        translations.append(target)

    def build(claims, reverse):
        rows = []
        for i, claim in enumerate(claims):
            pad = ' ' * (330 if (i < 9) != reverse else 3)
            if layout == 'table':
                rows.append(f'| {claim}{pad} |\n')
            elif layout == 'code':
                rows.append(f'{claim}{pad}\n')
            else:
                prefix = f'## Topic_{i}\n\n' if layout == 'heading' else ''
                rows.append(prefix + claim + '.' + pad + '\n\n')
        body = ''.join(rows)
        if layout == 'table':
            body = '| Value |\n| --- |\n' + body
        elif layout == 'code':
            body = '```text\n' + body + '```\n'
        elif layout == 'yfm':
            body = '{% note info %}\n\n' + body + '{% endnote %}\n'
        return body.replace('\n', newline)

    source, target = build(originals, False), build(translations, True)
    result, client = run(source, target, capacity=3700)
    assert result.complete, result.issues
    assert len(client.calls) > 1
    coverage(source, target, result, client)
    for original, translated in zip(originals, translations, strict=True):
        assert any(original in p['source'] and translated in p['target'] for _, p in client.calls)


def test_middle_failure_still_covers_both_tails_readonly(tmp_path):
    source = ''.join(f'Утверждение {i}.\n\n' for i in range(160))
    target = ''.join(f'Claim {i}.\n\n' for i in range(160))
    source_file, target_file = tmp_path / 'ru.md', tmp_path / 'en.md'
    source_file.write_text(source)
    target_file.write_text(target)
    before = (source_file.read_bytes(), target_file.read_bytes())
    result, client = run(source, target, capacity=3400, client=RecordingCritic(fail_at=2))
    assert len(client.calls) > 2
    assert not result.complete and not result.ok
    assert len(result.completed_parts) == len(result.parts) - 1
    coverage(source, target, result, client)
    assert (source_file.read_bytes(), target_file.read_bytes()) == before
    assert result.candidate_sha == 'fixed-candidate'
    with pytest.raises(FrozenInstanceError):
        result.complete = True


@pytest.mark.parametrize('separator', ['\u2028', '\u2029', '\x85', '\v', '\f'])
@pytest.mark.parametrize('newline', ['\r', '\n', '\r\n'])
def test_unicode_coordinates_for_deterministic_and_critic(separator, newline):
    text = 'First' + newline + 'Before' + separator + 'Ꙁ prose' + newline

    class QuotingCritic(RecordingCritic):
        def chat(self, messages, **kwargs):
            super().chat(messages, **kwargs)
            reply = {'complete': True, 'verdict': 'issues', 'issues': [{
                'path': PATH, 'problem': 'Untranslated prose', 'expected_fix': 'Translate prose',
                'severity': 'error', 'source': None,
                'target': {'start': 2, 'end': 2, 'quote': 'Ꙁ prose'},
            }]}
            return SimpleNamespace(content=json.dumps(reply), finish_reason='stop')

    result, _ = run(text, text, client=QuotingCritic())
    assert result.complete and not result.ok
    assert {i.code for i in result.issues} >= {'language', 'critic'}
    assert all(i.target.start == i.target.end == 2 for i in result.issues if i.target)


@pytest.mark.parametrize('letter', ['\u052f', '\u1c88', '\u1d78', '\u2dff', '\ua69f', '\ufe2f', '\U0001e08f'])
@pytest.mark.parametrize('code', [False, True])
def test_cyrillic_blocks_preserve_error_warning_policy(letter, code):
    text = f'`literal {letter}`' if code else f'## Label {letter}\n'
    result, _ = run(text, text)
    found = [i for i in result.issues if i.code == 'language']
    assert len(found) == 1
    assert found[0].severity == ('warning' if code else 'error')
    assert result.ok == code


@pytest.mark.parametrize('newline', ['\n', '\r', '\r\n'])
@pytest.mark.parametrize('directive', ['{% cut "Title" %}', '{% endif %}', '{% table %}'])
def test_multiline_inlinecode_inside_real_yfm(newline, directive):
    text = ('{% note info %}\n\nUse ``example\nliteral ' + directive
            + '\nend`` safely.\n\n{% endnote %}\n').replace('\n', newline)
    tokens = create_parser().parse(text)
    assert any(c.type == 'code_inline' and directive in c.content
               for t in tokens for c in (t.children or []))
    result, _ = run(text, text)
    assert not any(i.code == 'syntax' for i in result.issues)
    broken = text + '{% endcut %}' + newline
    result, _ = run(broken, broken)
    assert any(i.code == 'syntax' for i in result.issues)


def fm(value, field='description'):
    return f'---\n{field}: {value}\n---\n\nBody.\n'


@pytest.mark.parametrize('field', ['title', 'description'])
@pytest.mark.parametrize('damage', ['change', 'drop', 'duplicate', 'reorder'])
def test_decoded_front_matter_urls_are_protected(field, damage):
    source = fm('"Text \\u0068ttps://example.test/one https://example.test/two"', field)
    urls = {
        'change': 'https://example.test/other https://example.test/two',
        'drop': 'https://example.test/two',
        'duplicate': 'https://example.test/one https://example.test/one https://example.test/two',
        'reorder': 'https://example.test/two https://example.test/one',
    }
    target = fm(json.dumps('Text ' + urls[damage]), field)
    assert any(raw == 'https://example.test/one' for _, raw in protect(source).value_atoms)
    result, _ = run(source, target)
    assert any(i.code == 'protected' and i.severity == 'error' for i in result.issues)
    assert not result.ok


@pytest.mark.parametrize('field', ['title', 'description'])
def test_decoded_yaml_equivalence_and_validated_exact_url(field):
    source = fm('"Text \\u0068ttps://example.test/ru/page"', field)
    equivalent = fm("'Text https://example.test/ru/page'", field)
    assert run(source, equivalent)[0].ok
    translated = fm("'Text https://example.test/en/page'", field)
    assert run(source, translated, validated_url_replacements={
        'https://example.test/ru/page': 'https://example.test/en/page'})[0].ok


@pytest.mark.parametrize('suffix', ['/child', '?different=1', '#other'])
def test_validated_url_does_not_authorize_another_decoded_url(suffix):
    old, new = 'https://example.test/ru/page', 'https://example.test/en/page'
    source = fm(json.dumps('Text ' + old + suffix))
    target = fm(json.dumps('Text ' + new + suffix))
    assert any(raw == old + suffix for _, raw in protect(source).value_atoms)
    result, _ = run(source, target, validated_url_replacements={old: new})
    assert any(i.code == 'protected' for i in result.issues), result
    assert not result.ok


def test_validated_url_does_not_authorize_decoded_inline_code_change():
    old, new = 'https://example.test/ru/page', 'https://example.test/en/page'
    source = fm(json.dumps(f'Example `fetch("{old}")`'))
    target = fm(json.dumps(f'Example `fetch("{new}")`'))
    assert any(raw.startswith('`fetch') for _, raw in protect(source).value_atoms)
    result, _ = run(source, target, validated_url_replacements={old: new})
    assert any(i.code == 'protected' for i in result.issues), result


def test_real_link_replacement_preserves_decoded_code_example():
    old, new = 'https://example.test/ru/page', 'https://example.test/en/page'
    header = fm(json.dumps(f'Example `fetch("{old}")`'))
    source = header + f'\n[Guide]({old})\n'
    target = header + f'\n[Guide]({new})\n'
    result, _ = run(source, target, validated_url_replacements={old: new})
    assert result.ok, result.issues
