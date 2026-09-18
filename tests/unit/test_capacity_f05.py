"""Capacity uses complete prompts and model counts, without fixed chunk counts."""
import pytest

from ydbdoc_review.config.tokenization import ProviderTokenCounter
from ydbdoc_review.document import CapacityError, RequestBudget, chunk_document, protect
from ydbdoc_review.model import Endpoint


def endpoint(model='example'):
    return Endpoint('yandex_cloud', 'https://ai.api.cloud.yandex.net/v1', model, 'secret', 'folder')


def test_provider_counts_full_messages_and_raw_output_with_cache(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'tokens': [{'id': '1'}] * (8 if calls[-1][1]['modelUri'].endswith('/other') else 5)}

    def post(url, **kwargs):
        calls.append((url, kwargs['json']))
        assert kwargs['headers']['Authorization'] == 'Api-Key secret'
        return Response()

    monkeypatch.setattr('ydbdoc_review.config.tokenization.requests.post', post)
    counter = ProviderTokenCounter([endpoint(), endpoint('other'), endpoint()])
    messages = [{'role': 'system', 'content': 'YDB glossary: термин → term'},
                {'role': 'user', 'content': 'Текст. ⟦YDBDOC1⟧'}]
    assert counter(messages) == 8
    assert counter(messages) == 8
    assert len(calls) == 2
    assert calls[0][1]['messages'][0]['text'] == messages[0]['content']
    assert counter.count_output('ответ') == 8
    assert counter.count_output('ответ') == 8
    assert len(calls) == 4
    assert calls[-1][0].endswith('/tokenize')
    assert calls[-1][1]['text'] == 'ответ'


@pytest.mark.parametrize('failure', ['http', 'format'])
def test_tokenizer_failure_is_capacity_error_without_retry_or_generation(monkeypatch, failure):
    import requests
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        if failure == 'http':
            raise requests.HTTPError('secret response')
        return type('Response', (), {'raise_for_status': lambda self: None,
                                    'json': lambda self: {'tokens': 'invalid'}})()

    monkeypatch.setattr('ydbdoc_review.config.tokenization.requests.post', post)
    with pytest.raises(CapacityError, match='Tokenizer unavailable') as exc:
        ProviderTokenCounter([endpoint()])([{'role': 'user', 'content': 'a'}])
    assert 'secret' not in str(exc.value)
    assert calls == [1]


def test_unknown_provider_has_no_guessed_encoding():
    counter = ProviderTokenCounter([Endpoint('eliza', 'https://example.test', 'model', 'secret')])
    with pytest.raises(CapacityError, match='No verified tokenizer'):
        counter([{'role': 'user', 'content': 'text'}])


def test_whole_protected_file_first_even_when_raw_source_huge():
    document = protect('# Заголовок\n\n```sql\n' + 'SELECT 1;\n' * 5000 + '```\n')
    calls = []

    def fits(text):
        calls.append(text)
        return len(text) < 200

    chunks = chunk_document(document, fits)
    assert len(chunks) == 1
    assert calls == [document.text]
    assert chunks[0].source_end == len(document.source)


def test_complete_prompt_glossary_and_output_reserve_drive_split():
    document = protect('First sentence. Second sentence. Third sentence. Fourth sentence.')
    def count(messages):
        return sum(len(m['content']) for m in messages)

    def messages(text):
        return [{'role': 'system', 'content': 'YDB rules; glossary: ' + 'g' * 20},
                {'role': 'user', 'content': text}]
    budget = RequestBudget(111, 35, count, len)
    chunks = chunk_document(document, lambda text: budget.fits(messages(text), expected_output=text))
    assert len(chunks) > 1
    assert ''.join(c.text for c in chunks) == document.text
    for chunk in chunks:
        assert count(messages(chunk.text)) + 35 <= 111
        assert len(chunk.text) <= 35
    assert RequestBudget(111, 35, count, len).fits(messages('a' * 35), expected_output='a' * 35)
    assert not budget.fits(messages('a' * 36), expected_output='a' * 36)
    assert not budget.fits([*messages('a' * 35), {'role': 'system', 'content': 'x'}], expected_output='a' * 35)


def test_nonmonotonic_counter_does_not_reject_larger_safe_piece():
    document = protect('One. Two. Three.')
    first, second = document.boundaries[:2]
    accepted = {document.text[:second], document.text[second:]}
    assert document.text[:first] not in accepted
    chunks = chunk_document(document, lambda text: text in accepted)
    assert len(chunks) == 2


def test_markers_remain_indivisible_at_capacity_boundaries():
    document = protect('Alpha `code`. Beta `code`. Gamma `code`.')
    chunks = chunk_document(document, lambda text: len(text) <= 20)
    assert len(chunks) > 1
    assert ''.join(c.text for c in chunks) == document.text
    for atom in document.atoms:
        assert sum(atom.marker in c.text for c in chunks) == 1


@pytest.mark.parametrize('count', [-1, 1.5, True])
def test_invalid_model_count_fails_closed(count):
    with pytest.raises(CapacityError, match='Invalid request token count'):
        RequestBudget(100, 10, lambda messages: count).fits([])


def test_choice_scoping_excludes_unrelated_critic_tokenizer(monkeypatch):
    from ydbdoc_review.model import ModelChoice
    calls = []

    def count(self, e, method, payload):
        calls.append(e.model)
        return {'translation': 5, 'alternative': 7, 'critic': 999}[e.model]

    monkeypatch.setattr(ProviderTokenCounter, '_count', count)
    primary, alternative, critic = endpoint('translation'), endpoint('alternative'), endpoint('critic')
    counter = ProviderTokenCounter([primary, alternative, critic])
    budget = RequestBudget(100, 20, counter, counter.count_output).for_choice(ModelChoice(primary, alternative))
    assert budget.fits([{'role': 'user', 'content': 'text'}], expected_output='output')
    assert calls == ['translation', 'alternative', 'translation', 'alternative']


def test_runtime_explicit_tokenizer_injection(tmp_path, monkeypatch):
    import json

    from ydbdoc_review.config.defaults import default_runtime_data
    from ydbdoc_review.config.runtime import load_runtime
    monkeypatch.setenv('YANDEX_CLOUD_API_KEY_DOC_REVIEW', 'secret')
    monkeypatch.setenv('YANDEX_CLOUD_FOLDER_DOC_REVIEW', 'folder')
    path = tmp_path / 'runtime.json'
    path.write_text(json.dumps(default_runtime_data()))

    class LocalCounter:
        def __call__(self, messages):
            return 1

        def count_output(self, text):
            return 1

    counter = LocalCounter()
    budget = load_runtime(path, token_counter=counter).budget
    assert budget.count_tokens is counter
    assert budget.fits([{'role': 'user', 'content': 'text'}], expected_output='output')
