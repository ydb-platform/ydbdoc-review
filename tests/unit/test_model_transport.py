"""Contract tests at the real requests boundary, without live model calls."""

import json
from decimal import Decimal

import pytest
import requests

from ydbdoc_review.model import Endpoint, ModelChoice, ModelClient, ModelError


def response(status=200, data=None, *, text=None):
    result = requests.Response()
    result.status_code = status
    result._content = (text if text is not None else json.dumps(data)).encode()
    return result


def completion(content="translated", usage=None):
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": usage}


@pytest.fixture
def harness(monkeypatch):
    events, requests_seen, outcomes = [], [], []

    def send(session, request, **kwargs):
        events.append("http")
        requests_seen.append((request, kwargs))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(requests.Session, "send", send)
    client = ModelClient(
        record_request=lambda r: events.append(r), record_attempt=lambda r: events.append(r)
    )
    main = Endpoint("eliza", "https://eliza.example", "main", "secret")
    alternative = Endpoint(
        "yandex_cloud", "https://cloud.example/v1", "alternative", "key", "folder"
    )
    choice = ModelChoice(main, alternative)
    return client, choice, events, requests_seen, outcomes


def call(harness, operation="translation"):
    client, choice, *_ = harness
    return client.chat(
        [{"role": "user", "content": "source"}], operation=operation, choice=choice, max_tokens=2048
    )


@pytest.mark.parametrize(
    "failure",
    [
        requests.exceptions.Timeout(),
        requests.exceptions.ConnectionError(),
        requests.exceptions.ChunkedEncodingError(),
        response(500),
        response(503, text="not JSON"),
        response(599),
        response(408),
        response(404, {"error": {"code": "model_not_found"}}),
        response(410, {"error": {"code": "provider_unavailable"}}),
    ],
)
def test_only_technical_errors_allow_one_alternative(harness, failure):
    client, _, events, sent, outcomes = harness
    outcomes.extend([failure, response(data=completion())])
    assert call(harness).content == "translated"
    assert len(sent) == len(client.attempts) == 2
    assert [e if isinstance(e, str) else type(e).__name__ for e in events] == [
        "RequestRecord",
        "http",
        "AttemptRecord",
        "RequestRecord",
        "http",
        "AttemptRecord",
    ]
    assert client.attempts[0].error
    assert client.attempts[1].request.attempt == 1


@pytest.mark.parametrize("status", [300, 307, 400, 401, 403, 404, 422, 429])
def test_rejected_requests_do_not_fallback(harness, status):
    client, _, _, sent, outcomes = harness
    outcomes.append(
        response(status, {"error": {"code": "model_unavailable"}} if status != 404 else {})
    )
    with pytest.raises(ModelError):
        call(harness)
    assert len(sent) == len(client.attempts) == 1


@pytest.mark.parametrize(
    "bad",
    [None, {}, {"choices": []}, completion(None), completion("  "), completion([{"text": "text"}])],
)
def test_unusable_response_is_error_without_fallback(harness, bad):
    client, _, _, sent, outcomes = harness
    outcomes.append(response(data=bad))
    with pytest.raises(ModelError):
        call(harness, "critic")
    assert len(sent) == len(client.attempts) == 1
    assert client.attempts[0].request.operation == "critic"


def test_bad_http_json_does_not_fallback(harness):
    client, _, _, sent, outcomes = harness
    outcomes.append(response(text="{broken"))
    with pytest.raises(ModelError):
        call(harness)
    assert len(sent) == 1
    assert client.attempts[0].response_text == "{broken"


@pytest.mark.parametrize(
    "text", ["{bad critic JSON", "lost markers", "I cannot translate", "текст"]
)
def test_quality_is_not_a_transport_fallback(harness, text):
    _, _, _, sent, outcomes = harness
    outcomes.append(response(data=completion(text)))
    assert call(harness).content == text
    assert len(sent) == 1


def test_two_failures_never_make_third_call(harness):
    client, _, _, sent, outcomes = harness
    outcomes.extend([response(502), requests.exceptions.Timeout()])
    with pytest.raises(ModelError):
        call(harness)
    assert len(sent) == len(client.attempts) == 2


def test_no_alternative_one_call(harness):
    client, choice, _, sent, outcomes = harness
    outcomes.append(response(503))
    with pytest.raises(ModelError):
        client.chat([], operation="repair", choice=ModelChoice(choice.main), max_tokens=1)
    assert len(sent) == 1


def test_protocol_auth_tls_and_no_hidden_retries(harness):
    client, _, _, sent, outcomes = harness
    outcomes.extend([response(503), response(data=completion())])
    call(harness)
    first, first_args = sent[0]
    second, second_args = sent[1]
    assert first.url == "https://eliza.example/raw/internal/main/v1/chat/completions"
    assert first.headers["Authorization"] == "OAuth secret"
    assert "model" not in json.loads(first.body)
    assert second.url == "https://cloud.example/v1/chat/completions"
    assert second.headers["Authorization"] == "Bearer key"
    assert json.loads(second.body)["model"] == "gpt://folder/alternative"
    for args in (first_args, second_args):
        assert args["verify"]
        assert args["allow_redirects"] is False
        assert args["timeout"] == 120
    assert client._http.get_adapter(first.url).max_retries.total == 0
    assert "secret" not in repr(client.attempts)


def test_paid_failed_response_usage_and_operation_costs(harness):
    client, _, _, _, outcomes = harness
    client.cost_resolver = lambda endpoint, data: Decimal(data["billed_rub"])
    outcomes.extend(
        [
            response(
                503, {"usage": {"prompt_tokens": 41, "completion_tokens": 7}, "billed_rub": "1.2"}
            ),
            response(
                data={
                    **completion(usage={"prompt_tokens": 10, "completion_tokens": 2}),
                    "billed_rub": "0.3",
                }
            ),
            response(data={**completion(), "billed_rub": "2"}),
            response(data={**completion(), "billed_rub": "3"}),
        ]
    )
    call(harness)
    call(harness, "critic")
    call(harness, "repair")
    assert client.attempts[0].usage.input_tokens == 41
    assert client.attempts[0].usage.output_tokens == 7
    assert client.cost_breakdown() == {
        "translation": Decimal("1.5"),
        "critic": Decimal(2),
        "repair": Decimal(3),
        "total": Decimal("6.5"),
    }


def test_unknown_usage_cost_not_zero(harness):
    client, _, _, _, outcomes = harness
    outcomes.extend(
        [requests.exceptions.Timeout(), response(data=completion(usage={"prompt_tokens": 10}))]
    )
    call(harness)
    assert client.attempts[0].usage.input_tokens is None
    assert client.attempts[1].usage.input_tokens == 10
    assert client.attempts[1].usage.output_tokens is None
    assert all(a.usage.cost_rub is None for a in client.attempts)
    assert client.cost_breakdown() == {
        "translation": None,
        "critic": Decimal(0),
        "repair": Decimal(0),
        "total": None,
    }


def test_empty_paid_completion_keeps_usage(harness):
    client, _, _, _, outcomes = harness
    outcomes.append(response(data=completion("", {"prompt_tokens": 10, "completion_tokens": 200})))
    with pytest.raises(ModelError):
        call(harness)
    assert client.attempts[0].usage.output_tokens == 200


def fail_storage(record):
    raise OSError("storage unavailable")


def test_before_callback_failure_prevents_send(harness):
    client, _, _, sent, _ = harness
    client.record_request = fail_storage
    with pytest.raises(OSError):
        call(harness)
    assert not sent
    assert not client.attempts


@pytest.mark.parametrize("status", [200, 503])
def test_after_callback_failure_preserves_paid_attempt_without_fallback(harness, status):
    client, _, _, sent, outcomes = harness
    client.record_attempt = fail_storage
    outcomes.append(response(status, completion(usage={"prompt_tokens": 123})))
    with pytest.raises(OSError):
        call(harness)
    assert len(sent) == len(client.attempts) == 1
    assert client.attempts[0].usage.input_tokens == 123


def test_billing_failure_keeps_attempt_and_does_not_fallback(harness):
    client, _, _, sent, outcomes = harness
    client.cost_resolver = lambda *_: Decimal("NaN")
    outcomes.append(response(503, completion()))
    with pytest.raises(ValueError):
        call(harness)
    assert len(sent) == len(client.attempts) == 1


def test_tls_failure_is_not_retried(harness):
    _, _, _, sent, outcomes = harness
    outcomes.append(requests.exceptions.SSLError("certificate"))
    with pytest.raises(ModelError, match="TLS"):
        call(harness)
    assert len(sent) == 1


def test_alternative_cannot_repeat_identical_endpoint(harness):
    _, choice, *_ = harness
    with pytest.raises(ValueError, match="different"):
        ModelChoice(choice.main, choice.main)


@pytest.mark.parametrize(
    ('data', 'kind'),
    [
        (completion(None), 'empty'),
        (completion('  '), 'empty'),
        ({'choices': [{'message': {'content': None, 'reasoning_content': 'analysis'},
                       'finish_reason': 'length'}]}, 'length'),
        (completion({'text': 'not a string'}), 'invalid_format'),
        ({'choices': {}}, 'invalid_format'),
        ({'choices': [{'message': {}, 'finish_reason': 'stop'}]}, 'invalid_format'),
        ({'choices': [{'message': {'content': 'tool'}, 'finish_reason': 'tool_calls'}]},
         'invalid_format'),
    ],
)
def test_response_diagnoses_are_distinct_and_paid_once(harness, data, kind):
    client, _, events, sent, outcomes = harness
    data['usage'] = {'prompt_tokens': 12, 'completion_tokens': 8000,
                     'completion_tokens_details': {'reasoning_tokens': 8000}}
    client.cost_resolver = lambda *_: Decimal('6.4')
    outcomes.append(response(data=data))
    with pytest.raises(ModelError) as caught:
        call(harness)
    assert caught.value.kind == kind
    assert not caught.value.fallback_allowed
    assert len(sent) == len(client.attempts) == 1
    assert sum(type(e).__name__ == 'AttemptRecord' for e in events) == 1
    assert client.cost_breakdown()['total'] == Decimal('6.4')
    assert client.attempts[0].usage.output_tokens == 8000
    assert json.loads(client.attempts[0].response_text) == data
    if kind == 'length':
        assert 'reasoning_tokens=8000' in str(caught.value)


def test_partial_length_text_is_preserved_and_diagnosed(harness):
    client, _, _, sent, outcomes = harness
    data = completion('Useful partial translation', {'completion_tokens': 2048})
    data['choices'][0]['finish_reason'] = 'length'
    outcomes.append(response(data=data))
    result = call(harness)
    assert result.content == 'Useful partial translation'
    assert result.finish_reason == result.response_status == 'length'
    assert 'length limit' in result.attempt.error
    assert len(sent) == len(client.attempts) == 1


def test_explicit_yc_reasoning_control_and_output_reserve(harness):
    client, _, _, sent, outcomes = harness
    endpoint = Endpoint('yandex_cloud', 'https://cloud.example/v1', 'approved-model',
                        'key', 'folder', reasoning_effort='none')
    outcomes.append(response(data=completion()))
    result = client.chat([], operation='translation', choice=ModelChoice(endpoint), max_tokens=7000)
    assert result.response_status == 'complete'
    payload = json.loads(sent[0][0].body)
    assert payload['reasoning_effort'] == 'none'
    assert payload['max_tokens'] == 7000
    assert payload['model'] == 'gpt://folder/approved-model'
    assert 'thinking' not in payload


@pytest.mark.parametrize('effort', ['disabled', '', 0, False, []])
def test_invalid_reasoning_control_is_rejected_before_http(effort):
    with pytest.raises(ValueError, match='reasoning_effort'):
        Endpoint('yandex_cloud', 'https://cloud.example/v1', 'model', 'key', 'folder', effort)


def test_reasoning_control_not_guessed_for_other_provider():
    with pytest.raises(ValueError, match='reasoning_effort'):
        Endpoint('eliza', 'https://eliza.example', 'model', 'key', reasoning_effort='none')
