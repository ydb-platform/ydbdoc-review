"""Independent F04 acceptance, derived from REQUIREMENTS_RU §§3,3.1,7,9.1."""
import json
from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests

from ydbdoc_review.document import RequestBudget, translate_document
from ydbdoc_review.model import Endpoint, ModelChoice, ModelClient, ModelError

CHOICE = ModelChoice(Endpoint('yandex_cloud', 'https://primary.invalid/v1', 'primary', 'fake', 'folder'), Endpoint('yandex_cloud', 'https://alternative.invalid/v1', 'alternative', 'fake', 'folder'))

def completion(content=None, finish='stop', reasoning='private reasoning'):
    return {'choices': [{'message': {'content': content, 'reasoning_content': reasoning}, 'finish_reason': finish}], 'usage': {'prompt_tokens': 11, 'completion_tokens': 17, 'completion_tokens_details': {'reasoning_tokens': 13}}}

def response(body, status=200):
    r = requests.Response()
    r.status_code = status
    r._content = (body if isinstance(body, str) else json.dumps(body)).encode()
    return r

def setup(monkeypatch, actions):
    sent, saved = ([], [])
    client = ModelClient(record_request=sent.append, record_attempt=saved.append, cost_resolver=lambda endpoint, data: Decimal('1.25'))
    transport = Mock(side_effect=actions)
    monkeypatch.setattr(client._http, 'post', transport)
    return (client, transport, sent, saved)

def call(client):
    return client.chat([{'role': 'user', 'content': 'Translate'}], operation='translation', choice=CHOICE, max_tokens=8000)

@pytest.mark.parametrize('body,kind', [(completion('', 'length'), 'length'), (completion(None, 'length'), 'length'), (completion('  ', 'length'), 'length'), (completion(''), 'empty'), (completion(None), 'empty'), (completion(' \n'), 'empty'), (completion(['bad']), 'invalid_format'), ({'choices': []}, 'invalid_format'), ({'choices': [{'message': {}}]}, 'invalid_format'), ('not json', 'invalid_format'), (completion('text', 'tool_calls'), 'invalid_format')])
def test_unusable_diagnostic_single_billing_no_fallback(monkeypatch, body, kind):
    c, http, sent, saved = setup(monkeypatch, [response(body)])
    with pytest.raises(ModelError) as err:
        call(c)
    assert err.value.kind == kind
    assert not err.value.fallback_allowed
    assert http.call_count == len(sent) == len(saved) == len(c.attempts) == 1
    assert saved[0].response_text == response(body).text
    if isinstance(body, dict):
        assert c.cost_breakdown()['total'] == Decimal('1.25')
    else:
        assert c.cost_breakdown()['total'] is None
    if kind == 'length':
        assert 'reasoning_tokens=13' in str(err.value)
        assert saved[0].usage.output_tokens == 17

@pytest.mark.parametrize('first', [requests.Timeout(), requests.ConnectionError(), requests.exceptions.ChunkedEncodingError(), response({}, 408), response({}, 500), response({}, 503), response({'error': {'code': 'model_not_found'}}, 404), response({'error': {'code': 'provider_unavailable'}}, 410)])
def test_only_one_alternative_for_transport(monkeypatch, first):
    c, http, sent, saved = setup(monkeypatch, [first, response({}, 503)])
    with pytest.raises(ModelError):
        call(c)
    assert http.call_count == len(sent) == len(saved) == len(c.attempts) == 2
    assert [r.attempt for r in sent] == [0, 1]
    assert [r.model for r in sent] == ['primary', 'alternative']

@pytest.mark.parametrize('status', [301, 400, 401, 403, 404, 409, 410, 429])
def test_no_alternative_without_transport_authorization(monkeypatch, status):
    c, http, _sent, saved = setup(monkeypatch, [response({}, status)])
    with pytest.raises(ModelError):
        call(c)
    assert http.call_count == len(saved) == 1

@pytest.mark.parametrize('content,finish,status', [(None, 'length', 'missing'), ('Translated portion', 'length', 'truncated'), ('Translation', 'stop', 'complete')])
def test_document_keeps_missing_and_truncated_incomplete(monkeypatch, content, finish, status):
    c, http, _sent, saved = setup(monkeypatch, [response(completion(content, finish))])
    result = translate_document('Исходный текст.', path='doc.md', source_lang='ru', target_lang='en', client=c, choice=CHOICE, budget=RequestBudget(32768, 8000, lambda messages: 100))
    assert result.chunks[0].status == status
    assert result.unfinished == (status != 'complete')
    assert result.text == content
    assert http.call_count == len(saved) == 1
    assert c.cost_breakdown()['total'] == Decimal('1.25')

def test_damaged_marker_is_quality_not_fallback(monkeypatch):
    c, http, _sent, saved = setup(monkeypatch, [response(completion('Translation missing code marker'))])
    result = translate_document('Текст `protected`.', path='doc.md', source_lang='ru', target_lang='en', client=c, choice=CHOICE, budget=RequestBudget(32768, 8000, lambda messages: 100))
    assert result.unfinished and result.chunks[0].status == 'damaged'
    assert result.text == 'Translation missing code marker'
    assert http.call_count == len(saved) == 1

@pytest.mark.parametrize('where', ['attempt', 'billing'])
def test_callback_failure_does_not_rebill_or_fallback(monkeypatch, where):
    c, http, _sent, _saved = setup(monkeypatch, [response(completion(None, 'length'))])

    def fail(*args):
        raise RuntimeError('Persistence or billing failed')
    if where == 'attempt':
        c.record_attempt = fail
    else:
        c.cost_resolver = fail
    with pytest.raises(RuntimeError):
        call(c)
    assert http.call_count == len(c.attempts) == 1

def test_explicit_reasoning_control_is_per_endpoint(monkeypatch):
    c, http, sent, saved = setup(monkeypatch, [response({}, 503), response(completion('Result'))])
    choice = ModelChoice(Endpoint('yandex_cloud', 'https://primary.invalid', 'main', 'fake', 'folder', reasoning_effort='none'), CHOICE.alternative)
    answer = c.chat([{'role': 'user', 'content': 'Translate'}], operation='translation', choice=choice, max_tokens=8000)
    assert answer.content == 'Result'
    assert sent[0].payload['reasoning_effort'] == 'none'
    assert 'reasoning_effort' not in sent[1].payload
    assert all(r.payload['max_tokens'] == 8000 for r in sent)
    assert c.cost_breakdown()['total'] == Decimal('2.50')
    assert http.call_count == len(saved) == 2

@pytest.mark.parametrize('body', [completion(None, 'length'), completion(''), completion('not critic JSON'), completion('{"complete":true,"issues":[]}', 'length')])
def test_critic_error_never_success(monkeypatch, body):
    from ydbdoc_review.quality import check
    c, http, _sent, saved = setup(monkeypatch, [response(body)])
    result = check('Исходный текст.', 'Translated text.', path='doc.md', candidate_sha='fixed', target_lang='en', client=c, choice=CHOICE, budget=RequestBudget(32768, 8000, lambda messages: 100))
    assert not result.complete
    assert http.call_count == len(saved) == 1
