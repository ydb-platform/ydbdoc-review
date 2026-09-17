"""Independent T07 acceptance tests; mocks only, no production edits or T06 FM fixtures."""
import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from ydbdoc_review.document import RequestBudget
from ydbdoc_review.model import ModelError
from ydbdoc_review.parsing.markdown_parser import create_parser
from ydbdoc_review.quality import (
    Location, ReviewPart, check, parse_critic_response, structure_counts,
)

GOOD = {'complete': True, 'verdict': 'correct', 'issues': []}
PATH = 'en/adversarial.md'


class Critic:
    def __init__(self, reply=GOOD, finish='stop'):
        self.reply, self.finish, self.calls = reply, finish, []

    def chat(self, messages, **kwargs):
        assert kwargs['operation'] == 'critic', 'Read-only check called a repair operation'
        data = json.loads(messages[1]['content'])
        self.calls.append((messages, kwargs, data))
        reply = self.reply(data) if callable(self.reply) else self.reply
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=json.dumps(reply) if isinstance(reply, (dict, list)) else reply,
                               finish_reason=self.finish)


def run(source='Исходная фраза.', target='Translated phrase.', critic=None, capacity=100000, **kw):
    critic = critic or Critic()
    result = check(source, target, path=PATH, candidate_sha='immutable-test-sha', target_lang='en',
                   client=critic, choice=None,
                   budget=RequestBudget(capacity, 200, lambda m: len(str(m))), **kw)
    return result, critic


def issue(**overrides):
    item = dict(path=PATH, problem='The claim is missing.', expected_fix='Restore the claim.',
                severity='error', source=None, target=None)
    item.update(overrides)
    return dict(complete=True, verdict='issues', issues=[item])


@pytest.mark.parametrize('reply', [None, '', ' ', '```json\n{}\n```', 'false', 'NaN',
    {'complete': 1, 'verdict': 'correct', 'issues': []},
    {'complete': True, 'verdict': [], 'issues': []},
    {'complete': True, 'verdict': 'correct', 'issues': {}},
    {'complete': True, 'verdict': 'issues', 'issues': []},
    dict(GOOD, replacement='new text'), dict(GOOD, complete=False),
    issue(target={'start': 1.0, 'end': 1, 'quote': 'Translated'}),
    issue(target={'start': 1, 'end': 1, 'quote': 'invented'}),
    issue(target={'start': 1, 'end': 999, 'quote': 'Translated'}),
    issue(source={'start': 1, 'end': 1, 'quote': 'Translated'}),
    issue(path='en/wrong.md'), issue(severity='info'), issue(expected_fix=None),
    ModelError('offline simulated timeout'),
    '{"complete":true,"verdict":"correct","issues":[],"complete":true}',
])
def test_bad_critic_is_incomplete_without_repair(reply):
    result, client = run(critic=Critic(reply))
    assert not result.complete and not result.ok
    assert len(client.calls) == 1
    assert all(c[1]['operation'] == 'critic' for c in client.calls)


@pytest.mark.parametrize('finish', ['length', 'content_filter', 'tool_calls'])
def test_unfinished_response_is_not_success(finish):
    assert not run(critic=Critic(finish=finish))[0].complete


def test_equal_counts_meaning_loss_detected_by_content_fixture():
    def respond(data):
        assert data['source_counts'] == data['target_counts']
        assert 'three replicas' in data['source'] and 'one replica' in data['target']
        return issue(source=dict(start=1, end=1, quote='three replicas'),
                     target=dict(start=1, end=1, quote='one replica'))
    result, _ = run('Use three replicas.', 'Use one replica.', Critic(respond))
    assert result.complete and not result.ok
    for found in result.issues:
        found.validate('Use three replicas.', 'Use one replica.')


def test_paragraph_merger_is_only_count_signal():
    result, client = run('Alpha.\n\nBeta.\n', 'Alpha. Beta.\n')
    assert result.ok
    assert client.calls[0][2]['mismatches']['paragraphs'] == [2, 1]
    mismatch = next(i for i in result.issues if i.code == 'structure')
    assert mismatch.severity == 'info' and mismatch.source is mismatch.target is None


def test_unknown_table_loss_not_assigned_invented_coordinates():
    src = '| h |\n|---|\n| retained |\n| omitted |\n'
    tgt = '| h |\n|---|\n| retained |\n'
    result, client = run(src, tgt, Critic(issue()))
    assert client.calls[0][2]['mismatches']['rows'] == [3, 2]
    assert result.complete and not result.ok
    assert all(i.target is None and i.source is None for i in result.issues)


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
def test_repeated_quote_requires_actual_line_and_reviewed_part(newline):
    text = newline.join(['same', 'different', 'same']) + newline
    with pytest.raises(ValueError):
        Location(2, 2, 'same').validate(text)
    content = json.dumps(issue(target=dict(start=3, end=3, quote='same')))
    with pytest.raises(ValueError):
        parse_critic_response(content, path=PATH, source=text, target=text,
                              part=ReviewPart(0, 4, 0, 4))
    complete, found = parse_critic_response(content, path=PATH, source=text, target=text,
                                          part=ReviewPart(0, len(text), 0, len(text)))
    assert complete
    found[0].validate(text, text)


def test_counts_nested_lists_soft_wrap_code_table_yfm():
    text = ('# Heading\n\nA soft\nwrap.\n\n- first\n  continued\n  - nested\n- last\n\n'
            '| A | B |\n|---|---|\n| C | D |\n| E | F |\n\n'
            '{% note info %}\n\n{% cut "Details" %}\n\n'
            '```text\n# not heading\n- not list\n| not row |\n```\n\n'
            '{% endcut %}\n\n{% endnote %}\n')
    counts = structure_counts(text)
    assert (counts.headings, counts.list_items, counts.tables, counts.rows, counts.code, counts.yfm) == (1, 3, 1, 3, 1, 2)
    assert counts.paragraphs == 4
    assert structure_counts(text.replace('A soft\nwrap.', 'A soft wrap.')) == counts


@pytest.mark.parametrize('kind', ['code', 'table', 'yfm', 'prose'])
def test_long_complete_raw_coverage_with_unequal_parts(kind):
    def document(n):
        rows = [f'Entry {i:03d} carries a distinct claim.\n' for i in range(n)]
        if kind == 'code':
            return '```text\n' + ''.join(rows) + '```\n'
        if kind == 'table':
            return '| h |\n|---|\n' + ''.join('| ' + r.rstrip() + ' |\n' for r in rows)
        if kind == 'yfm':
            return '{% note info %}\n\n' + '\n'.join(rows) + '\n{% endnote %}\n'
        return '\n'.join(rows)
    source, target = document(47), document(59)
    result, client = run(source, target, capacity=3400)
    assert result.complete, result.issues
    assert len(client.calls) == len(result.parts) > 1
    for side, text in [('source', source), ('target', target)]:
        covered = set()
        for part, (messages, _, data) in zip(result.parts, client.calls, strict=True):
            a, b = getattr(part, side + '_start'), getattr(part, side + '_end')
            assert data[side] == text[a:b]
            assert len(str(messages)) + 200 <= 3400
            covered.update(range(a, b))
        assert covered == set(range(len(text)))


def test_failed_middle_part_does_not_skip_tail():
    client = Critic(lambda data: 'bad' if len(client.calls) == 2 else GOOD)
    result, _ = run('Source sentence.\n\n' * 100, 'Target sentence.\n\n' * 100, client, capacity=3100)
    assert len(result.parts) > 2
    assert len(client.calls) == len(result.parts)
    assert not result.complete
    assert result.completed_parts == tuple(i for i in range(len(result.parts)) if i != 1)


def test_oversize_is_incomplete_not_silent_truncation():
    result, client = run('x' * 9000, 'y', capacity=3000)
    assert not result.complete and not result.ok
    assert not client.calls


def test_corresponding_claims_must_reach_same_request_under_length_skew():
    # Same 8 sections, identical prose/order; harmless whitespace skews raw lengths.
    # Stable identifiers let us check correspondence without a live language model.
    source = ''.join(f'## ITEM_{i:02d}\n\nShared claim {i}. ' + (' ' * (560 if i < 4 else 16)) + '\n\n' for i in range(8))
    target = ''.join(f'## ITEM_{i:02d}\n\nShared claim {i}. ' + (' ' * (16 if i < 4 else 560)) + '\n\n' for i in range(8))
    result, client = run(source, target, capacity=3700)
    assert result.complete
    missing = [i for i in range(8) if not any(
        f'Shared claim {i}.' in c[2]['source'] and f'Shared claim {i}.' in c[2]['target']
        for c in client.calls)]
    assert not missing, f'Claims never compared with their counterpart: {missing}; parts={result.parts}'


@pytest.mark.parametrize('text', ['`данные`', '```text\nданные\n```\n', '```yaml\nkey: данные\n```\n'])
def test_protected_cyrillic_is_warning_readonly(text):
    result, client = run(text, text)
    assert result.ok
    assert [i.severity for i in result.issues if i.code == 'language'] == ['warning']
    assert client.calls[0][2]['target'] == text
    with pytest.raises(FrozenInstanceError):
        result.complete = False


@pytest.mark.parametrize('text', ['# Остаток\n', '| H |\n|---|\n| Остаток |\n',
    '{% note info %}\n\nОстаток\n\n{% endnote %}\n',
    '```python\nx = 1 # Остаток\n```\n', '```mermaid\ngraph TD\nA[Остаток] --> B[End]\n```\n'])
def test_translatable_cyrillic_is_error(text):
    result, _ = run(text, text)
    assert not result.ok
    language = [i for i in result.issues if i.code == 'language']
    assert language and all(i.severity == 'error' for i in language)
    for found in result.issues:
        found.validate(text, text)


def test_cyrillic_extended_b_is_detected():
    # U+A640 is CYRILLIC CAPITAL LETTER ZEMLYA, prose rather than protected code.
    result, _ = run('A letter.', 'A letter Ꙁ.')
    assert any(i.code == 'language' and i.severity == 'error' for i in result.issues)


def test_deterministic_coordinates_with_unicode_line_separator_are_valid():
    text = 'Prefix\u2028Остаток'
    result, _ = run(text, text)
    assert any(i.code == 'language' for i in result.issues)
    for found in result.issues:
        found.validate(text, text)


def test_multiline_inline_code_yfm_example_is_not_unclosed_container():
    text = 'Use `the directive\nliteral {% note info %}\ninside code` as an example.\n'
    tokens = create_parser().parse(text)
    assert any(child.type == 'code_inline' and '{% note info %}' in child.content
               for token in tokens for child in (token.children or []))
    result, _ = run(text, text)
    assert not any(i.code == 'syntax' for i in result.issues), result.issues


@pytest.mark.parametrize(('source', 'target', 'expect_error'), [
    ('Таблица.', 'Relation.', False), ('Таблица.', 'Table.', True),
    ('Супертаблица.', 'Supertable.', False), ('`таблица`', '`таблица`', False),
    ('таблица', '`relation`', True),
])
def test_explicit_glossary_whole_prose_term(source, target, expect_error):
    result, client = run(source, target, glossary={'таблица': 'relation'})
    assert any(i.code == 'glossary' for i in result.issues) is expect_error
    assert client.calls[0][2]['glossary'] == {'таблица': 'relation'}


def test_ok_is_document_local_not_links_or_build_green():
    text = '[a](missing.md)\n'
    result, _ = run(text, text)
    assert result.ok and result.candidate_sha == 'immutable-test-sha'
    assert 'not a substitute' in type(result).ok.__doc__


def test_critic_quote_on_physical_line_after_unicode_separator_is_accepted():
    source, target = 'Alpha\u2028claim.', 'Beta\u2028claim.'
    result, _ = run(source, target, Critic(issue(
        target=dict(start=1, end=1, quote='claim.'))))
    assert result.complete, result.issues


def test_no_style_or_baseline_heuristic_blocks_clean_prose():
    result, client = run('This is a sentence.', 'It is a sentence.')
    assert result.ok and len(client.calls) == 1
    result, _ = run('Existing Остаток.', 'Existing Остаток.')
    assert not result.ok


@pytest.mark.parametrize(('source', 'target'), [
    ('Use `a` then `b`.', 'Use `b` then `a`.'),
    ('Use `a`.', 'Use `a` and `a`.'),
    ('Use `a`.', 'Use prose.'),
    ('```sql\nSELECT 1;\n```', '```sql\nSELECT 2;\n```'),
])
def test_protected_loss_duplication_order_and_code_mutation(source, target):
    result, _ = run(source, target)
    assert any(i.code == 'protected' and i.severity == 'error' for i in result.issues)
    assert not result.ok

# Dispatcher integration regression: T06 now exposes decoded YAML value atoms.
@pytest.mark.parametrize('field', ['title', 'description'])
def test_decoded_front_matter_url_remains_protected(field):
    from ydbdoc_review.quality import deterministic_checks
    source = f'---\n{field}: "Ссылка https://example.com/original"\nconfig: safe\n---\nText\n'
    target = f'---\n{field}: "Link https://example.com/changed"\nconfig: safe\n---\nText\n'
    issues = deterministic_checks(source, target, path='page.md', target_lang='en')
    assert any(issue.code == 'protected' for issue in issues), issues
    approved = deterministic_checks(source, target, path='page.md', target_lang='en',
        validated_url_replacements={'https://example.com/original': 'https://example.com/changed'})
    assert not any(issue.code == 'protected' for issue in approved), approved
