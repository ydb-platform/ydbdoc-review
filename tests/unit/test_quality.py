# ruff: noqa: RUF001
import json
from types import SimpleNamespace

import pytest

from ydbdoc_review.document import RequestBudget
from ydbdoc_review.model import ModelError
from ydbdoc_review.parsing.markdown_parser import parse_review_blocks
from ydbdoc_review.quality import (
    Location,
    ReviewPart,
    check,
    parse_critic_response,
    review_parts,
    structure_counts,
)

CORRECT = {'complete': True, 'verdict': 'correct', 'issues': []}


class Client:
    def __init__(self, response=CORRECT):
        self.response = response
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        response = self.response(messages) if callable(self.response) else self.response
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(content=response if isinstance(response, str) else json.dumps(response),
                               finish_reason='stop')


def run(source='Текст.', target='Text.', client=None, **kwargs):
    return check(source, target, path='en/a.md', candidate_sha='abc', target_lang='en',
                 client=client or Client(), choice=None,
                 budget=kwargs.pop('budget', RequestBudget(100000, 1000, lambda m: len(str(m)))),
                 **kwargs)


def test_nested_counts_and_soft_wrap():
    text = '# H\n\nFirst\nwrapped.\n\n- one\n- two\n\n| a | b |\n|---|---|\n| c | d |\n\n```sql\nSELECT 1;\n```\n\n{% note info %}\n\nNested.\n\n{% endnote %}\n'
    counts = structure_counts(text)
    assert (counts.headings, counts.paragraphs, counts.list_items, counts.tables,
            counts.rows, counts.code, counts.yfm) == (1, 4, 2, 1, 2, 1, 1)
    assert parse_review_blocks(text)[2].counts.list_items == 2
    assert structure_counts('A\nB\n').paragraphs == 1
    assert structure_counts('A\n\nB\n').paragraphs == 2


def test_different_paragraph_count_not_loss_or_incomplete():
    client = Client()
    result = run('Первое.\n\nВторое.\n', 'First. Second.\n', client)
    assert result.ok
    assert result.complete
    assert next(i for i in result.issues if i.code == 'structure').target is None
    payload = json.loads(client.calls[0][0][1]['content'])
    assert payload['mismatches']['paragraphs'] == [2, 1]
    assert len(client.calls) == 1


def issue_response(**kwargs):
    issue = dict(path='en/a.md', problem='Meaning missing', expected_fix='Restore the claim.',
                 severity='error', source=None, target=None)
    issue.update(kwargs)
    return dict(complete=True, verdict='issues', issues=[issue])


def test_equal_counts_still_critic_semantic_issue():
    result = run('Два узла нужны.', 'One node is needed.', Client(issue_response(
        source=dict(start=1, end=1, quote='Два узла'),
        target=dict(start=1, end=1, quote='One node'))))
    assert result.source_counts == result.target_counts
    assert result.complete and not result.ok
    assert result.issues[0].source.quote == 'Два узла'


@pytest.mark.parametrize('localized', [True, False])
def test_missing_table_row_localized_or_explicitly_unknown(localized):
    source = '| a |\n|---|\n| first |\n| lost |\n'
    target = '| a |\n|---|\n| first |\n'
    result = run(source, target, Client(issue_response(
        source=dict(start=4, end=4, quote='| lost |') if localized else None)))
    assert result.complete and not result.ok
    issue = next(i for i in result.issues if i.code == 'critic')
    assert issue.target is None
    assert bool(issue.source) == localized


@pytest.mark.parametrize('response', ['', 'not json', '{}', '[]', 'null',
    {'complete': True, 'verdict': 'issues', 'issues': []},
    {'complete': True, 'verdict': 'correct', 'issues': [{}]},
    issue_response(problem=''), issue_response(path='another.md'),
    issue_response(target=dict(start=1, end=1, quote='invented')),
    issue_response(target=dict(start=99, end=99, quote='Text.')),
    issue_response(target=dict(start=True, end=1, quote='Text.')),
    dict(CORRECT, suggested_text='rewrite'), ModelError('timeout'),
    dict(CORRECT, complete=False)])
def test_malformed_empty_error_incomplete_no_repair(response):
    client = Client(response)
    result = run(client=client)
    assert not result.complete and not result.ok
    assert len(client.calls) == 1
    assert client.calls[0][1]['operation'] == 'critic'


def test_response_requires_all_issue_fields():
    response = issue_response()
    del response['issues'][0]['expected_fix']
    assert not run(client=Client(response)).complete


def test_locations_exact_crlf_and_repeat():
    text = 'same\r\nother\r\nsame\r\n'
    Location(3, 3, 'same').validate(text)
    with pytest.raises(ValueError):
        Location(2, 2, 'same').validate(text)
    with pytest.raises(ValueError):
        parse_critic_response(json.dumps(issue_response(target=dict(start=3, end=3, quote='same'))),
                              path='en/a.md', source=text, target=text,
                              part=ReviewPart(0, 6, 0, 6))


def test_all_long_raw_text_is_covered_including_target_tail_and_gaps():
    source = ''.join(f'Source {i}.\n\n' for i in range(100))
    target = ''.join(f'Target {i}.\n\n' for i in range(113)) + 'TAIL\n'
    client = Client()
    budget = RequestBudget(3400, 500, lambda m: len(str(m)))
    result = run(source, target, client, budget=budget)
    assert result.complete
    assert len(result.parts) > 1
    for text, side in [(source, 'source'), (target, 'target')]:
        covered = set()
        for part in result.parts:
            covered.update(range(getattr(part, side + '_start'), getattr(part, side + '_end')))
        assert covered == set(range(len(text)))
    assert len(client.calls) == len(result.completed_parts)
    assert all(budget.fits(m) for m, _ in client.calls)
    assert any('TAIL' in m[1]['content'] for m, _ in client.calls)


def test_one_failed_part_does_not_skip_remaining_parts():
    def respond(messages):
        return 'bad' if len(client.calls) == 1 else CORRECT
    client = Client(respond)
    result = run('s\n\n' * 100, 't\n\n' * 100, client,
                 budget=RequestBudget(2800, 500, lambda m: len(str(m))))
    assert len(client.calls) > 1
    assert not result.complete
    assert result.completed_parts == tuple(range(1, len(client.calls)))


def test_whole_file_first_and_indivisible_capacity_failure():
    assert len(review_parts('a\n\nb', 'c\n\nd', fits=lambda p: True)) == 1
    client = Client()
    result = run('a' * 10000, 'b', client, budget=RequestBudget(2500, 500, lambda m: len(str(m))))
    assert not result.complete and not client.calls


@pytest.mark.parametrize('text', ['```text\nкод\n```\n', '`код`',
                                  '```yaml\nkey: "код"\n```\n'])
def test_protected_cyrillic_warning_only(text):
    result = run(text, text)
    assert result.ok
    assert any(i.severity == 'warning' and i.code == 'language' for i in result.issues)


@pytest.mark.parametrize('text', ['# Заголовок', '| a |\n|---|\n| подпись |',
    '```python\nx = 1 # комментарий\n```', '---\ntitle: Заголовок\n---\nText.',
    '```mermaid\ngraph TD\n A[Подпись] --> B[End]\n```'])
def test_prose_comments_labels_cyrillic_error(text):
    result = run(text, text)
    assert any(i.severity == 'error' and i.code == 'language' for i in result.issues)


@pytest.mark.parametrize(('source', 'target'), [
    ('Use `id`.', 'Use `other`.'), ('```sql\nSELECT 1;\n```', '```sql\nSELECT 2;\n```'),
    ('A [link](a.md)', 'A [link](b.md)'), ('{{ var }}', '{{ changed }}'),
    ('Use `id`.', 'Use `id` and `id`.'), ('`a` and `b`', '`b` and `a`'),
])
def test_protected_damage(source, target):
    assert any(i.code == 'protected' for i in run(source, target).issues)


def test_translatable_comment_preserves_code():
    result = run('```python\nx = 1 # комментарий\n```', '```python\nx = 1 # comment\n```')
    assert result.ok


def test_markers_glossary_and_validated_url_mapping():
    assert any(i.code == 'markers' for i in run(target='Text ⟦C1⟧').issues)
    assert any(i.code == 'glossary' for i in run('таблица', 'table', glossary={'таблица': 'relation'}).issues)
    assert run('таблица', 'relation', glossary={'таблица': 'relation'}).ok
    assert run('[текст](ru/a.md)', '[text](en/a.md)',
               validated_url_replacements={'ru/a.md': 'en/a.md'}).ok


@pytest.mark.parametrize('text', ['```sql\nSELECT 1;', '{% note info %}\nText',
    '{% endnote %}', '{% cut "Title" %}\n{% endnote %}', '#|\n|| a ||'])
def test_syntax_without_baseline_suppression(text):
    assert any(i.code == 'syntax' for i in run(text, text).issues)


def test_no_text_mutation_and_storage_errors_propagate():
    source, target = 'Текст.', 'Text.'
    result = run(source, target)
    assert (source, target) == ('Текст.', 'Text.')
    assert result.candidate_sha == 'abc'
    with pytest.raises(RuntimeError, match='storage'):
        run(client=Client(RuntimeError('storage')))


def test_truncated_critic_is_incomplete():
    class Truncated(Client):
        def chat(self, *args, **kwargs):
            return SimpleNamespace(content=json.dumps(CORRECT), finish_reason='length')
    assert not run(client=Truncated()).complete


def test_long_single_table_and_prose_are_fully_covered():
    for text in ['| a |\n|---|\n' + '| data |\n' * 300,
                 'A sentence. ' * 300,
                 '```text\n' + 'line\n' * 300 + '```\n']:
        client = Client()
        result = run(text, text, client, budget=RequestBudget(3200, 500, lambda m: len(str(m))))
        assert result.complete
        assert len(result.parts) > 1
        assert set().union(*(set(range(p.source_start, p.source_end)) for p in result.parts)) == set(range(len(text)))


def test_code_indentation_is_protected():
    result = run('```python\n  call()\n```', '```python\ncall()\n```')
    assert any(i.code == 'protected' for i in result.issues)


def test_nested_fence_and_inline_yfm_example_are_not_syntax_errors():
    for text in ['> ```text\n> example\n> ```\n', 'Use `{% note info %}` to start a note.']:
        assert not any(i.code == 'syntax' for i in run(text, text).issues)


def test_empty_target_cannot_hide_complete_loss():
    result = run('Source content', '')
    assert not result.ok
    assert any(i.code == 'missing_text' for i in result.issues)
    assert run('', '').ok


def test_duplicate_json_keys_are_malformed():
    client = Client('{"complete": false, "complete": true, "verdict": "correct", "issues": []}')
    assert not run(client=client).complete


def test_yfm_table_nested_rows_and_crlf_coordinates():
    counts = structure_counts('{% cut "Title" %}\n\n#|\n|| a | b ||\n|| c | d ||\n|#\n\n{% endcut %}')
    assert (counts.tables, counts.rows, counts.yfm) == (1, 2, 1)
    result = run('English\r\n\r\nКириллица\r\n', 'English\r\n\r\nКириллица\r\n')
    issue = next(i for i in result.issues if i.code == 'language')
    assert issue.target.start == issue.target.end == 3
    issue.validate('English\r\n\r\nКириллица\r\n', 'English\r\n\r\nКириллица\r\n')
