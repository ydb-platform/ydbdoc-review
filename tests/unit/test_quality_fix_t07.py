"""T07 bug regressions: real preparation/checking, only the critic is mocked."""
import json
from types import SimpleNamespace

import pytest

from ydbdoc_review.document import RequestBudget
from ydbdoc_review.parsing.markdown_parser import create_parser
from ydbdoc_review.quality import Location, check


class Critic:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        assert kwargs['operation'] == 'critic'
        self.calls.append((messages, json.loads(messages[1]['content'])))
        return SimpleNamespace(content='{"complete":true,"verdict":"correct","issues":[]}',
                               finish_reason='stop')


def run(source, target, capacity=3700):
    client = Critic()
    budget = RequestBudget(capacity, 200, lambda messages: len(str(messages)))
    result = check(source, target, path='en/fix.md', candidate_sha='unchanged', target_lang='en',
                   client=client, choice=None, budget=budget)
    assert all(budget.fits(messages) for messages, _ in client.calls)
    assert len(client.calls) == len(result.parts)
    return result, client


def assert_coverage(source, target, result, client):
    for side, text in [('source', source), ('target', target)]:
        cursor = 0
        for part, (_, payload) in zip(result.parts, client.calls, strict=True):
            start, end = getattr(part, side + '_start'), getattr(part, side + '_end')
            assert start == cursor
            assert payload[side] == text[start:end]
            cursor = end
        assert cursor == len(text)


@pytest.mark.parametrize('layout', ['headings', 'paragraphs', 'sentences'])
def test_translated_counterparts_stay_together_under_opposite_length_skew(layout):
    source_claims = [f'Утверждение {i} сохранено.' for i in range(12)]
    target_claims = [f'Claim {i} is preserved.' for i in range(12)]

    def document(claims, source):
        return ''.join((f'## Section_{i}\n\n' if layout == 'headings' else '') + claim
                       + ' ' * (500 if (i < 6) == source else 2)
                       + ('\n\n' if layout != 'sentences' else ' ')
                       for i, claim in enumerate(claims))

    source, target = document(source_claims, True), document(target_claims, False)
    result, client = run(source, target)
    assert result.complete, result.issues
    assert len(client.calls) > 1
    for original, translated in zip(source_claims, target_claims, strict=True):
        assert any(original in payload['source'] and translated in payload['target']
                   for _, payload in client.calls)
    assert_coverage(source, target, result, client)


def test_structural_alignment_without_shared_words_or_numbers():
    source_claims = [f'Начало {letter} конец.' for letter in 'абвгдежзик']
    target_claims = [f'Beginning {letter} end.' for letter in 'abcdefghij']
    source = ''.join(s + ' ' * (450 if i < 5 else 1) + '\n\n'
                     for i, s in enumerate(source_claims))
    target = ''.join(s + ' ' * (1 if i < 5 else 450) + '\n\n'
                     for i, s in enumerate(target_claims))
    result, client = run(source, target)
    assert result.complete
    for original, translated in zip(source_claims, target_claims, strict=True):
        assert any(original in p['source'] and translated in p['target'] for _, p in client.calls)
    assert_coverage(source, target, result, client)


def test_ambiguous_unequal_long_units_are_incomplete_without_invented_omissions():
    result, client = run('Исходная фраза.\n\n' * 100, 'Translated statement.\n\n' * 107)
    assert not result.complete and not result.ok
    assert not client.calls
    assert any(i.code == 'critic_incomplete' and 'corresponding' in i.problem for i in result.issues)
    assert all(i.source is None and i.target is None for i in result.issues)
    assert all(i.code in {'structure', 'critic_incomplete'} for i in result.issues)


def test_reordered_unique_anchors_cannot_be_reported_as_complete():
    claims = [f'Claim {i}. ' + ' ' * 300 + '\n\n' for i in range(12)]
    result, client = run(''.join(claims), ''.join(reversed(claims)))
    assert not result.complete and not client.calls
    assert any('reordered' in i.problem for i in result.issues)


def test_long_anchored_tail_is_fully_reviewed_without_truncation():
    source = ''.join(f'Claim {i}.\n\n' for i in range(30))
    target = source + ''.join(f'Extra claim {i}.\n\n' for i in range(30, 160))
    result, client = run(source, target, 3100)
    assert result.complete
    assert_coverage(source, target, result, client)


def test_paragraph_merge_retains_sentence_counterparts():
    source = ''.join(f'Исходная фраза {i}.\n\n' for i in range(90))
    target = ' '.join(f'Translated statement {i}.' for i in range(90))
    result, client = run(source, target)
    assert result.complete and result.ok
    for i in range(90):
        assert any(f'фраза {i}.' in p['source'] and f'statement {i}.' in p['target']
                   for _, p in client.calls)
    assert_coverage(source, target, result, client)


@pytest.mark.parametrize('separator', ['\u2028', '\u2029', '\x85', '\v', '\f'])
@pytest.mark.parametrize('newline', ['\n', '\r', '\r\n'])
def test_unicode_separators_do_not_change_published_lines(separator, newline):
    text = f'Prefix{separator}Остаток{newline}Second{separator}Ꙁ{newline}'
    result, _ = run(text, text)
    language = [i for i in result.issues if i.code == 'language']
    assert [i.target.start for i in language] == [1, 2]
    for issue in result.issues:
        issue.validate(text, text)
    Location(2, 2, f'Second{separator}Ꙁ{newline}').validate(text)
    with pytest.raises(ValueError):
        Location(2, 2, 'Остаток').validate(text)


@pytest.mark.parametrize('letter', ['\u0410', '\u0500', '\u1c80', '\u1d2b', '\u1d78',
                                   '\u2de0', '\ua640', '\ufe2e', '\U0001e030'])
@pytest.mark.parametrize('protected', [False, True])
def test_all_cyrillic_blocks_and_phonetic_letters(letter, protected):
    text = f'`literal {letter}`' if protected else f'Literal {letter}.'
    result, _ = run(text, text)
    found = [i for i in result.issues if i.code == 'language']
    assert len(found) == 1
    assert found[0].severity == ('warning' if protected else 'error')
    found[0].validate(text, text)
    assert result.ok == protected


@pytest.mark.parametrize('newline', ['\n', '\r', '\r\n'])
@pytest.mark.parametrize('marker', ['`', '``'])
@pytest.mark.parametrize('directive', ['{% note info %}', '{% endcut %}', '#|', '|#'])
def test_multiline_inline_code_owns_yfm_delimiters(newline, marker, directive):
    text = newline.join([f'Use {marker}literal', f'literal {directive}', f'code{marker} here.', ''])
    tokens = create_parser().parse(text)
    assert any(c.type == 'code_inline' and directive in c.content
               for t in tokens for c in (t.children or []))
    result, _ = run(text, text)
    assert not any(i.code == 'syntax' for i in result.issues)
    # Ignoring code must not also suppress a real unmatched directive outside it.
    outside = text + '{% note info %}' + newline
    result, _ = run(outside, outside)
    found = [i for i in result.issues if i.code == 'syntax']
    assert len(found) == 1 and 'Unclosed YFM note' in found[0].problem
    found[0].validate(outside, outside)


@pytest.mark.parametrize(('source_value', 'target_value', 'damaged'), [
    ('"Ссылка https://example.com/a"', '"Link https://example.com/b"', True),
    ('"Ссылка https://example.com/a"', "'Link https://example.com/a'", False),
    ('"Ссылка https://example.com/a"', '"Link"', True),
    ('"Код `a`"', '"Code `b`"', True),
    ('"Код `a`"', '"Code `a` and `a`"', True),
    ('"Код `a` затем `b`"', '"Code `b` then `a`"', True),
    ('"Ссылка \\u0068ttps://example.com/a"', '"Link https://example.com/a"', False),
])
def test_decoded_front_matter_atoms_keep_identity_order_and_multiplicity(source_value, target_value, damaged):
    source = f'---\ntitle: {source_value}\n---\nText\n'
    target = f'---\ntitle: {target_value}\n---\nText\n'
    result, _ = run(source, target)
    assert any(i.code == 'protected' for i in result.issues) == damaged
