"""Independent T05 acceptance checks; mock only the HTTP adapter boundary."""

import json
from decimal import Decimal

import pytest
import requests

from ydbdoc_review.model import Endpoint, ModelChoice, ModelClient, ModelError

SECRET = "t05-credential-must-not-be-logged"


def reply(status=200, content="result", usage=None, raw=None):
    response = requests.Response()
    response.status_code = status
    response._content = (
        raw
        if raw is not None
        else json.dumps(
            {
                "choices": [{"message": {"content": content}}],
                "usage": usage,
            }
        )
    ).encode()
    return response


@pytest.fixture
def transport(monkeypatch):
    events, outcomes, sent = [], [], []

    def send(adapter, request, **kwargs):
        assert adapter.max_retries.total == 0
        assert events[-1][0] == "before"
        events.append(("http", request.url))
        sent.append(request)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        outcome.request = request
        return outcome

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    client = ModelClient(
        record_request=lambda record: events.append(("before", record)),
        record_attempt=lambda record: events.append(("after", record)),
    )
    choice = ModelChoice(
        Endpoint("eliza", "https://main.invalid", "same-model", SECRET),
        Endpoint("yandex_cloud", "https://other.invalid/v1", "same-model", SECRET, "folder"),
    )
    yield client, choice, events, outcomes, sent
    client.close()


def invoke(transport, operation="critic"):
    client, choice, *_ = transport
    return client.chat(
        [{"role": "user", "content": "source"}],
        operation=operation,
        choice=choice,
        max_tokens=500,
    )


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 410, 422, 429])
def test_unconfirmed_http_failure_is_terminal_and_paid_usage_survives(transport, status):
    client, _, events, outcomes, sent = transport
    outcomes.append(reply(status, usage={"prompt_tokens": 19, "completion_tokens": 7}))
    with pytest.raises(ModelError):
        invoke(transport)
    assert len(sent) == len(client.attempts) == 1
    attempt = client.attempts[0]
    assert attempt.usage.input_tokens == 19
    assert attempt.usage.output_tokens == 7
    assert attempt.usage.cost_rub is None
    assert [event[0] for event in events] == ["before", "http", "after"]


@pytest.mark.parametrize("code", ["model_not_found", "model_unavailable", "provider_unavailable"])
@pytest.mark.parametrize("status", [404, 410])
def test_confirmed_unavailable_has_one_alternative_and_never_third(transport, status, code):
    client, _, events, outcomes, sent = transport
    outcomes.extend(
        [
            reply(status, raw=json.dumps({"error": {"code": code}})),
            reply(503, usage={"prompt_tokens": 4}),
        ]
    )
    with pytest.raises(ModelError):
        invoke(transport, "repair")
    assert len(sent) == len(client.attempts) == 2
    assert [event[0] for event in events] == ["before", "http", "after"] * 2
    assert [record.request.operation for record in client.attempts] == ["repair", "repair"]
    assert [record.request.provider for record in client.attempts] == ["eliza", "yandex_cloud"]
    assert client.attempts[1].usage.input_tokens == 4


@pytest.mark.parametrize("raw", ["null", "[]", "1", '"str"', '{"choices":{}}', "{broken"])
def test_bad_protocol_json_is_never_a_successful_critic_or_fallback(transport, raw):
    client, _, _, outcomes, sent = transport
    outcomes.append(reply(raw=raw))
    with pytest.raises(ModelError):
        invoke(transport)
    assert len(sent) == len(client.attempts) == 1
    assert client.attempts[0].response_text == raw


@pytest.mark.parametrize("content", ['{"ok": bad JSON}', "lost __ATOM__", "I refuse", "Кириллица"])
def test_semantic_quality_does_not_trigger_extra_transport(transport, content):
    _, _, _, outcomes, sent = transport
    outcomes.append(reply(content=content))
    assert invoke(transport).content == content
    assert len(sent) == 1


def test_callback_mutation_cannot_corrupt_wire_or_retained_usage(transport):
    client, _, _, outcomes, sent = transport
    original_before = client.record_request

    def before(record):
        original_before(record)
        record.payload["messages"][0]["content"] = "corruption"

    def after(record):
        record.usage.raw["prompt_tokens"] = 999

    client.record_request = before
    client.record_attempt = after
    outcomes.append(reply(usage={"prompt_tokens": 12}))
    invoke(transport)
    assert json.loads(sent[0].body)["messages"][0]["content"] == "source"
    assert client.attempts[0].usage.raw["prompt_tokens"] == 12


@pytest.mark.parametrize("status", [200, 500])
def test_after_storage_failure_preserves_paid_cost_and_never_replays(transport, status):
    client, _, _, outcomes, sent = transport
    client.cost_resolver = lambda *_: Decimal("0.75")

    def unavailable(record):
        raise OSError("YDB is unavailable")

    client.record_attempt = unavailable
    outcomes.append(reply(status, usage={"prompt_tokens": 80, "completion_tokens": 4}))
    with pytest.raises(OSError, match="YDB"):
        invoke(transport, "translation")
    assert len(sent) == len(client.attempts) == 1
    assert client.attempts[0].usage.output_tokens == 4
    assert client.cost_breakdown()["translation"] == Decimal("0.75")
    assert client.cost_breakdown()["total"] == Decimal("0.75")


def test_second_before_storage_failure_keeps_first_paid_attempt(transport):
    client, _, events, outcomes, sent = transport
    original_before = client.record_request

    def before(record):
        if record.attempt:
            raise OSError("second request cannot be saved")
        original_before(record)

    client.record_request = before
    outcomes.append(reply(502, usage={"prompt_tokens": 22}))
    with pytest.raises(OSError):
        invoke(transport)
    assert len(sent) == len(client.attempts) == 1
    assert client.attempts[0].usage.input_tokens == 22
    assert [event[0] for event in events] == ["before", "http", "after"]


def test_operations_are_independent_and_unknown_cost_poisons_only_its_category(transport):
    client, _, _, outcomes, _ = transport
    costs = iter([Decimal("1"), None, Decimal("3"), Decimal("4")])
    client.cost_resolver = lambda *_: next(costs)
    outcomes.extend([reply(500), reply(), reply(), reply()])
    invoke(transport, "translation")
    invoke(transport, "critic")
    invoke(transport, "repair")
    assert client.cost_breakdown() == {
        "translation": None,
        "critic": Decimal("3"),
        "repair": Decimal("4"),
        "total": None,
    }


@pytest.mark.parametrize("failure", [requests.Timeout, requests.ConnectionError])
def test_exception_credentials_do_not_leak_and_both_calls_are_journaled(transport, failure, caplog):
    client, choice, events, outcomes, sent = transport
    outcomes.extend([failure(SECRET), failure(SECRET)])
    with pytest.raises(ModelError) as caught:
        invoke(transport)
    assert len(sent) == len(client.attempts) == 2
    assert SECRET not in repr(choice)
    assert SECRET not in repr(events)
    assert SECRET not in repr(client.attempts)
    assert SECRET not in str(caught.value)
    assert SECRET not in caplog.text
    assert caught.value.__context__ is None
