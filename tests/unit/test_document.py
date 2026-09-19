"""T06 contracts, independent of the legacy orchestration."""
# ruff: noqa: RUF001
from pathlib import Path
from types import SimpleNamespace

import pytest

from ydbdoc_review.document import (
    CapacityError,
    RequestBudget,
    chunk_document,
    protect,
    restore,
    translate_document,
    translate_files,
    translation_messages,
)
from ydbdoc_review.model import Endpoint, ModelChoice

CHOICE = ModelChoice(Endpoint('eliza', 'https://example.test', 'test', 'test'))


class Client:
    def __init__(self, actions=()):
        self.calls = []
        self.actions = iter(actions)

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        action = next(self.actions, None)
        if isinstance(action, Exception):
            raise action
        text = messages[-1]['content'].split('\n\n', 1)[1]
        return SimpleNamespace(content=action(text) if callable(action) else text,
                               finish_reason='stop')


def budget(size=100000):
    return RequestBudget(size + 100000, 100000,
                         lambda messages: sum(len(m['content']) for m in messages))


def run(source, client=None, **kwargs):
    return translate_document(source, path='x.md', source_lang='ru', target_lang='en',
                              client=client or Client(), choice=CHOICE, budget=budget(), **kwargs)


def test_whole_file_source_only_even_dense_table():
    source = '# Заголовок\n\n| A | Б |\n|---|---|\n' + '| Текст | `code` |\n' * 100
    client = Client()
    result = run(source, client)
    assert result.text == source
    assert not result.issues and not result.unfinished
    assert len(client.calls) == 1
    assert client.calls[0][1]['operation'] == 'translation'
    # No old target input exists on either primary API.
    import inspect
    assert 'target' not in inspect.signature(translate_document).parameters
    assert 'target' not in inspect.signature(translate_files).parameters


def test_protected_bytes_and_translatable_islands_crlf():
    source = ('---\r\ntitle: "Заголовок"\r\ndescription: >-\r\n  Описание\r\n'
              'config: "значение" # keep\r\n---\r\n'
              '# Заголовок {#stable}\r\n\r\n'
              '[Подпись](https://example.test/ru/a?q=1 "TITLE") `код` {{ variable }}\r\n'
              '![Картинка](image.png =100x200) <https://example.test/ru/raw>\r\n'
              '```python\r\nx = "строка" # Комментарий\r\n```\r\n'
              '```yaml\r\nkey: "значение" # Комментарий\r\n```\r\n'
              '```mermaid\r\ngraph TD\r\nA[Начало] --> B[Конец]\r\n```\r\n')
    words = {'Заголовок': 'Heading', 'Описание': 'Description', 'Подпись': 'Label',
             'Картинка': 'Image', 'Комментарий': 'Comment', 'Начало': 'Start', 'Конец': 'End'}
    def translate(text):
        for old, new in words.items():
            assert old in text
            text = text.replace(old, new)
        assert 'значение' not in text and 'строка' not in text and 'https://' not in text
        return text
    result = run(source, Client([translate]))
    expected = source
    for old, new in words.items():
        expected = expected.replace(old, new)
    assert result.text == expected
    assert not result.issues


def test_order_rejected_and_damaged_response_retained_without_retry():
    source = '`a` текст `b`\n'
    def swap(t):
        return t.replace('⟦C1⟧', '@').replace('⟦C2⟧', '⟦C1⟧').replace('@', '⟦C2⟧')
    client = Client([swap])
    result = run(source, client)
    assert result.issues and 'MarkerError' in result.issues[0].problem
    assert result.text == swap(protect(source).text)
    assert result.chunks[0].response == result.text
    assert len(client.calls) == 1


def test_chunking_is_lossless_ordered_and_atoms_indivisible():
    p = protect('Абзац с `атомом`. Ещё предложение.\r\n\r\n' * 30)
    chunks = chunk_document(p, lambda s: len(s) <= 90)
    assert len(chunks) > 1
    assert ''.join(c.text for c in chunks) == p.text
    assert ''.join(restore(p, c.text, expected=c.text) for c in chunks) == p.source
    assert [c.index for c in chunks] == list(range(len(chunks)))
    for c in chunks:
        assert c.text.count('⟦') == c.text.count('⟧')
        assert len(c.text) <= 90


def test_indivisible_sentence_over_budget_fails_explicitly():
    with pytest.raises(CapacityError):
        chunk_document(protect('Оченьдлинноеслово' * 100), lambda s: len(s) < 20)


def test_complete_request_prompt_and_output_reserve_counted():
    b = RequestBudget(20, 5, lambda m: sum(len(x['content']) for x in m))
    assert b.fits([{'content': 'x' * 15}])
    assert not b.fits([{'content': 'x' * 16}])
    assert not b.fits(translation_messages('x', source_lang='ru', target_lang='en', path='x'))


def test_later_chunk_error_retains_previous_and_remaining_successes():
    source = 'Абзац один.\n\nАбзац два.\n\nАбзац три.\n'
    overhead = sum(len(m['content']) for m in translation_messages('', source_lang='ru', target_lang='en', path='x.md'))
    snapshots = []
    client = Client([lambda t: t.replace('один', 'one'), RuntimeError('later'), None])
    result = translate_document(source, path='x.md', source_lang='ru', target_lang='en',
                                client=client, choice=CHOICE, budget=budget(overhead + 22),
                                on_progress=snapshots.append)
    assert result.unfinished and result.issues
    assert 'one' in result.text and 'три' in result.text
    assert 'два' not in result.text
    assert snapshots[0].text and snapshots[0].unfinished
    assert result.chunks[1].text is None


def test_later_file_error_does_not_discard_received_result():
    snapshots = []
    results = translate_files([('a.md', 'Первый.'), ('b.md', 'Второй.')],
                              source_lang='ru', target_lang='en',
                              client=Client([lambda t: 'First.', RuntimeError('failed')]),
                              choice=CHOICE, budget=budget(), on_progress=snapshots.append)
    assert results['a.md'].text == 'First.'
    assert results['b.md'].unfinished and results['b.md'].text is None
    assert snapshots[0].path == 'a.md' and snapshots[0].text == 'First.'


def test_empty_source_no_model_and_storage_error_propagates():
    client = Client()
    assert run('', client).text == '' and not client.calls
    def fail(_):
        raise OSError('store down')
    with pytest.raises(OSError, match='store down'):
        run('Text', client, on_progress=fail)


def test_comment_injection_reported_no_hidden_repair():
    client = Client([lambda t: t.replace('Комментарий', 'Comment\nx = 999 # injected')])
    result = run('```python\nx = 1 # Комментарий\n```\n', client)
    assert result.text and result.issues
    assert 'protected code' in result.issues[0].problem
    assert len(client.calls) == 1


FIXTURES = Path(__file__).parents[1] / 'fixtures'
CORPUS = sorted(FIXTURES.rglob('*.md'))
assert CORPUS, 'Real document corpus must not be empty'


@pytest.mark.parametrize('path', CORPUS, ids=lambda p: str(p.relative_to(FIXTURES)))
def test_corpus_raw_roundtrip(path):
    source = path.read_bytes().decode('utf-8')
    p = protect(source)
    assert restore(p, p.text).encode('utf-8') == path.read_bytes()


def test_output_capacity_and_truncation_are_explicit():
    b = RequestBudget(10000, 10, lambda m: sum(len(x['content']) for x in m))
    assert not b.fits([{'content': 'short input'}], expected_output='x' * 11)
    class Truncated(Client):
        def chat(self, messages, **kwargs):
            return SimpleNamespace(content='Partial ⟦broken', finish_reason='length')
    result = run('Original.', Truncated())
    assert result.text == 'Partial ⟦broken' and result.unfinished and result.issues


@pytest.mark.parametrize('source', ['Literal ⟦C1⟧ marker.', 'Unclosed ⟦marker', 'Closing ⟧.'])
def test_literal_source_markers_roundtrip(source):
    p = protect(source)
    assert restore(p, p.text) == source


def test_nested_yfm_prose_and_titles_exposed():
    source = ('{% note info "Заметка" %}\n\nТекст.\n\n'
              '{% cut "Подробнее" %}\n\n**Описание**.\n\n{% endcut %}\n'
              '{% endnote %}\n\n{% list tabs %}\n\n- Вкладка\n\n  Абзац.\n\n{% endlist %}\n')
    p = protect(source)
    for word in ['Заметка', 'Текст', 'Подробнее', 'Описание', 'Вкладка', 'Абзац']:
        assert word in p.text
    assert '{%' not in p.text
    assert restore(p, p.text) == source


def test_sentence_chunk_separators_survive_response_trimming():
    p = protect('First sentence. Second sentence. Third sentence.')
    chunks = chunk_document(p, lambda text: len(text) < 30)
    assert len(chunks) > 1
    assert ''.join(restore(p, c.text.strip(), expected=c.text) for c in chunks) == p.source


def test_unknown_mermaid_is_explicit_and_no_code_is_exposed():
    source = '```mermaid\npie\n    "Название" : 12\n```\n'
    p = protect(source)
    assert 'Название' not in p.text
    result = run(source)
    assert result.text == source and result.issues
    assert 'Unsupported Mermaid' in result.issues[0].problem


def test_wrapped_paragraph_is_not_split_at_arbitrary_linebreak():
    p = protect('An unfinished sentence\ncontinues on this line without punctuation')
    with pytest.raises(CapacityError):
        chunk_document(p, lambda text: len(text) <= 35)
