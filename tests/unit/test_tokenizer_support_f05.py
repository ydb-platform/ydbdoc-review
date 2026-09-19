"""Offline reproduction: unsupported YC deployments must never reach Tokenizer."""
import pytest

from ydbdoc_review.config.tokenization import ProviderTokenCounter
from ydbdoc_review.document import CapacityError, RequestBudget
from ydbdoc_review.model import Endpoint


def endpoint(model):
    return Endpoint('yandex_cloud', 'https://ai.api.cloud.yandex.net/v1', model, 'secret', 'folder')


@pytest.mark.parametrize('models', [
    ['deepseek-v32'], ['deepseek-v4-flash'], ['unknown'],
    ['yandexgpt-5-pro', 'deepseek-v32'], ['deepseek-v32', 'yandexgpt-5-pro'],
])
@pytest.mark.parametrize('method', ['messages', 'output', 'empty_output'])
def test_unsupported_model_rejected_before_any_http(monkeypatch, models, method):
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        return type('Response', (), {'raise_for_status': lambda self: None,
                                    'json': lambda self: {'tokens': [{'id': '1'}]}})()

    monkeypatch.setattr('ydbdoc_review.config.tokenization.requests.post', post)
    counter = ProviderTokenCounter([endpoint(model) for model in models])
    with pytest.raises(CapacityError, match='No verified tokenizer'):
        if method == 'messages':
            counter([{'role': 'user', 'content': 'source'}])
        else:
            counter.count_output('' if method == 'empty_output' else 'translation')
    assert calls == []


def test_empty_endpoint_configuration_is_explicit_capacity_error():
    with pytest.raises(CapacityError, match='No configured models'):
        ProviderTokenCounter([])([{'role': 'user', 'content': 'source'}])


def test_alternative_output_limit_and_full_prompt_are_both_checked(monkeypatch):
    calls = []

    def post(url, **kwargs):
        payload = kwargs['json']
        calls.append((url, payload))
        alternative = payload['modelUri'].endswith('/yandexgpt-5.1')
        count = (9 if alternative else 4) if 'messages' in payload else (6 if alternative else 3)
        return type('Response', (), {'raise_for_status': lambda self: None,
                                    'json': lambda self: {'tokens': [{'id': '1'}] * count}})()

    monkeypatch.setattr('ydbdoc_review.config.tokenization.requests.post', post)
    counter = ProviderTokenCounter([endpoint('yandexgpt-5-pro'), endpoint('yandexgpt-5.1')])
    messages = [{'role': 'system', 'content': 'YDB. Glossary: термин — term'},
                {'role': 'user', 'content': 'Текст ⟦YDBDOC1⟧'}]
    assert not RequestBudget(20, 5, counter, counter.count_output).fits(messages, expected_output='out')
    assert not RequestBudget(14, 6, counter, counter.count_output).fits(messages, expected_output='out')
    assert RequestBudget(15, 6, counter, counter.count_output).fits(messages, expected_output='out')
    assert len(calls) == 4
    assert calls[0][1]['messages'] == [{'role': m['role'], 'text': m['content']} for m in messages]
    assert calls[2][1]['text'] == 'out'
