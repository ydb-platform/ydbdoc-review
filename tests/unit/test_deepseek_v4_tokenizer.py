"""Real pinned tokenizer regression; sockets disabled by the normal test config."""
from dataclasses import replace
from pathlib import Path

import pytest

from ydbdoc_review.config import deepseek_v4
from ydbdoc_review.config.tokenization import ProviderTokenCounter
from ydbdoc_review.document import CapacityError, RequestBudget
from ydbdoc_review.model import Endpoint


def endpoint():
    return Endpoint('yandex_cloud', 'https://ai.api.cloud.yandex.net/v1',
                    'deepseek-v4-flash', 'secret', 'folder')


def test_reference_encoding_and_real_token_ids():
    messages = [{'role': 'system', 'content': 'You are a helpful assistant.'},
                {'role': 'user', 'content': 'What is 2+2?'}]
    text = deepseek_v4.render_messages(messages, thinking=False)
    assert text == ('<\uff5cbegin▁of▁sentence\uff5c>You are a helpful assistant.'
                    '<\uff5cUser\uff5c>What is 2+2?<\uff5cAssistant\uff5c></think>')
    ids = deepseek_v4._tokenizer().encode(text, add_special_tokens=False).ids
    assert ids[0] == 0
    assert ids[-2:] == [128804, 128822]
    assert len(ids) == deepseek_v4.count_messages(messages, 'none')
    assert deepseek_v4.count_output('hello') == 1
    assert deepseek_v4.count_output('') == 0


@pytest.mark.parametrize('text', ['Привет, YDB!', 'Hello, YDB!', '🙂漢字',
                                 '⟦YDBDOC000001⟧ SELECT key FROM table;', 'abc ' * 20000])
def test_full_prompt_capacity_includes_glossary_and_output(text):
    messages = [{'role': 'system', 'content': 'YDB glossary: транзакция = transaction'},
                {'role': 'user', 'content': text}]
    counter = ProviderTokenCounter([endpoint()])
    size, output = counter(messages), counter.count_output(text)
    assert size > deepseek_v4.count_output(text)
    assert RequestBudget(size + output, output, counter, counter.count_output).fits(messages, expected_output=text)
    assert not RequestBudget(size + output - 1, output, counter, counter.count_output).fits(messages, expected_output=text)
    assert size == max(deepseek_v4.count_messages(messages, mode) for mode in ('none', 'high'))


def test_yandex_fallback_keeps_its_own_tokenizer(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs['json']))
        return type('Response', (), {'raise_for_status': lambda self: None,
                                    'json': lambda self: {'tokens': [{}] * 91}})()
    monkeypatch.setattr('ydbdoc_review.config.tokenization.requests.post', post)
    counter = ProviderTokenCounter([endpoint(), replace(endpoint(), model='yandexgpt-5-pro')])
    assert counter([{'role': 'user', 'content': 'hello'}]) == 91
    assert counter.count_output('hello') == 91
    assert all(p['modelUri'].endswith('/yandexgpt-5-pro') for _, p in calls)
    assert len(calls) == 2


@pytest.mark.parametrize('messages', [[{'role': 'assistant', 'content': 'x'}],
                                      [{'role': 'user', 'content': 'x', 'tools': []}], []])
def test_unimplemented_framing_fails_closed(messages):
    with pytest.raises(CapacityError):
        ProviderTokenCounter([endpoint()])(messages)


def test_unknown_reasoning_fails_before_fallback_http():
    counter = ProviderTokenCounter([replace(endpoint(), model='yandexgpt-5-pro'),
                                    replace(endpoint(), reasoning_effort='xhigh')])
    with pytest.raises(CapacityError, match='reasoning_effort'):
        counter([{'role': 'user', 'content': 'hello'}])


@pytest.mark.parametrize('missing', [False, True])
def test_missing_or_corrupt_data_fails_closed(monkeypatch, missing):
    deepseek_v4._tokenizer.cache_clear()
    def bad_read(self):
        if missing:
            raise FileNotFoundError()
        return b'{}'
    monkeypatch.setattr(Path, 'read_bytes', bad_read)
    try:
        with pytest.raises(CapacityError, match='unavailable or corrupt'):
            deepseek_v4.count_output('hello')
    finally:
        deepseek_v4._tokenizer.cache_clear()
