# ruff: noqa: RUF001

import asyncio
import json
from types import SimpleNamespace

import pytest

import ydbdoc_review.translation.one_pass as one_pass_module
from ydbdoc_review.llm.acquisition import (
    AcquisitionBlockedError,
    AcquisitionController,
    AcquisitionExhaustedError,
    AcquisitionProtocolError,
)
from ydbdoc_review.llm.errors import (
    LLMConfigError,
    LLMModelUnavailableError,
    LLMRequestError,
    LLMRetryableRequestError,
)
from ydbdoc_review.translation.local_repair import run_bounded_local_repair
from ydbdoc_review.translation.model_policy import (
    ModelPair,
    TranslationJobManifest,
    TranslationModelPolicy,
)
from ydbdoc_review.translation.one_pass import (
    OnePassTranslationError,
    translate_ru_to_en_once,
)

MANIFEST = TranslationJobManifest(TranslationModelPolicy(
    translate=ModelPair("translate-primary", "translate-fallback"),
    critic=ModelPair("critic-primary", "critic-fallback"),
    repair=ModelPair("repair-primary", "repair-fallback"),
))


class RecordingClient:
    def __init__(self, transform):
        self.transform = transform
        self.calls = []

    def chat_once(self, messages, *, explicit_model, role, **kwargs):
        self.calls.append((messages, role, explicit_model))
        if role == "critic":
            return SimpleNamespace(content=json.dumps({"findings": []}))
        payload = json.loads(messages[-1]["content"])
        segments = [
            {"id": item["id"], "text": self.transform(item["text"])}
            for item in payload["segments"]
        ]
        return SimpleNamespace(content=json.dumps({"segments": segments}))


def test_many_segments_use_exactly_one_model_call():
    client = RecordingClient(
        lambda text: text.replace("Заголовок", "Heading")
        .replace("Абзац", "Paragraph")
        .replace("один", "one")
        .replace("два", "two")
    )
    result = translate_ru_to_en_once(
        "# Заголовок\n\nАбзац один.\n\nАбзац два.\n",
        client,
        file_path="ydb/docs/ru/page.md",
        manifest=MANIFEST,
    )

    assert result.model_calls == 1
    assert result.prose_count == 3
    assert [role for _, role, _ in client.calls].count("translate") == 1
    assert [role for _, role, _ in client.calls].count("critic") == 1
    assert "Heading" in result.text
    assert result.text.count("Paragraph") == 2


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"segments": []}',
        '{"segments": [{"id": "s0001", "text": "Text"}, {"id": "extra", "text": "x"}]}',
    ],
)
def test_invalid_response_fails_without_retry(content):
    class InvalidClient:
        calls = 0

        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            self.calls += 1
            return SimpleNamespace(content=content)

    client = InvalidClient()
    with pytest.raises(OnePassTranslationError):
        translate_ru_to_en_once("Текст.\n", client, file_path="ydb/docs/ru/page.md", manifest=MANIFEST)
    assert client.calls == 2


def test_lost_or_duplicated_atom_token_blocks_file():
    class PrimaryCorruptFallbackValid(RecordingClient):
        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            if role == "critic":
                return super().chat_once(
                    messages, explicit_model=explicit_model, role=role, **kwargs
                )
            self.calls.append((messages, role, explicit_model))
            payload = json.loads(messages[-1]["content"])
            translated = []
            for item in payload["segments"]:
                text = item["text"].replace("Ссылка", "Link")
                if explicit_model == "translate-primary":
                    text = text.replace("⟦LEND_1⟧", "⟦LEND_1⟧⟦LEND_1⟧")
                translated.append({"id": item["id"], "text": text})
            return SimpleNamespace(content=json.dumps({"segments": translated}))

    client = PrimaryCorruptFallbackValid(lambda text: text)
    result = translate_ru_to_en_once(
        "[Ссылка](target.md)\n",
        client,
        file_path="ydb/docs/ru/page.md",
        manifest=MANIFEST,
    )

    assert "[Link](target.md)" in result.text
    assert [model for _, role, model in client.calls if role == "translate"] == [
        "translate-primary",
        "translate-fallback",
    ]


class AcquisitionClient:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def chat_once(self, messages, *, explicit_model, role, **kwargs):
        self.calls.append((messages, role, explicit_model))
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(content=outcome)


@pytest.mark.parametrize(
    "control_flow",
    [asyncio.CancelledError(), KeyboardInterrupt(), SystemExit()],
)
def test_acquisition_propagates_control_flow_unchanged(control_flow):
    client = AcquisitionClient([control_flow])

    with pytest.raises(type(control_flow)) as exc_info:
        AcquisitionController(
            client,
            MANIFEST.model_policy.translate,
            role="translate",
            parser=_valid_content,
        ).acquire([])

    assert exc_info.value is control_flow
    assert len(client.calls) == 1


def _valid_content(response):
    if not response.content:
        raise AcquisitionProtocolError("empty response")
    if response.content != "valid":
        raise AcquisitionProtocolError("invalid schema")
    return response.content


def test_acquisition_retries_transport_twice_per_model_and_accepts_first_valid():
    client = AcquisitionClient(
        [
            LLMRetryableRequestError("timeout", status_code=408),
            LLMRetryableRequestError("server", status_code=503),
            LLMRetryableRequestError("rate limit", status_code=429),
            "valid",
        ]
    )
    result = AcquisitionController(
        client, MANIFEST.model_policy.translate, role="translate", parser=_valid_content
    ).acquire([{"role": "user", "content": "same payload"}])

    assert result.payload == "valid"
    assert result.model_slug == "translate-fallback"
    assert [call[2] for call in client.calls] == [
        "translate-primary",
        "translate-primary",
        "translate-fallback",
        "translate-fallback",
    ]
    assert [attempt.classification for attempt in result.attempts] == [
        "transport_error",
        "transport_error",
        "transport_error",
        "accepted",
    ]
    assert all(call[0][0]["content"] == "same payload" for call in client.calls)


def test_acquisition_protocol_error_advances_without_same_model_retry():
    client = AcquisitionClient(["invalid", "valid"])
    result = AcquisitionController(
        client, MANIFEST.model_policy.critic, role="critic", parser=_valid_content
    ).acquire([])

    assert result.model_slug == "critic-fallback"
    assert [call[2] for call in client.calls] == [
        "critic-primary",
        "critic-fallback",
    ]
    assert [attempt.classification for attempt in result.attempts] == [
        "protocol_error",
        "accepted",
    ]


def test_acquisition_unavailable_advances_immediately_and_stays_in_role():
    client = AcquisitionClient([LLMModelUnavailableError("not available"), "valid"])
    result = AcquisitionController(
        client, MANIFEST.model_policy.repair, role="repair", parser=_valid_content
    ).acquire([])

    assert result.model_slug == "repair-fallback"
    assert [call[1:] for call in client.calls] == [
        ("repair", "repair-primary"),
        ("repair", "repair-fallback"),
    ]


@pytest.mark.parametrize(
    "error",
    [
        LLMRequestError("HTTP 400 invalid request"),
        LLMRequestError("HTTP 401 unauthorized"),
        LLMRequestError("HTTP 403 forbidden"),
    ],
)
def test_acquisition_auth_and_bad_request_block_without_fallback(error):
    client = AcquisitionClient([error])
    with pytest.raises(AcquisitionBlockedError) as raised:
        AcquisitionController(
            client, MANIFEST.model_policy.translate, role="translate", parser=_valid_content
        ).acquire([])

    assert len(client.calls) == 1
    assert raised.value.attempts[0].classification == "blocking_error"


def test_acquisition_exhaustion_has_at_most_four_explicit_network_attempts():
    client = AcquisitionClient(
        [
            LLMRetryableRequestError("503", status_code=503),
            LLMRetryableRequestError("503", status_code=503),
            LLMRetryableRequestError("503", status_code=503),
            LLMRetryableRequestError("503", status_code=503),
        ]
    )
    with pytest.raises(AcquisitionExhaustedError) as raised:
        AcquisitionController(
            client, MANIFEST.model_policy.translate, role="translate", parser=_valid_content
        ).acquire([])

    assert len(client.calls) == 4
    assert len(raised.value.attempts) == 4


def test_acquisition_rejects_same_model_for_primary_and_fallback():
    with pytest.raises(LLMConfigError, match="distinct primary and fallback"):
        TranslationJobManifest(TranslationModelPolicy(
            translate=ModelPair("same", "same"),
            critic=MANIFEST.model_policy.critic,
            repair=MANIFEST.model_policy.repair,
        ))


def test_one_pass_runs_bounded_one_block_repair_and_recritic():
    class RepairingClient:
        def __init__(self):
            self.critic_calls = 0

        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            body = json.loads(messages[-1]["content"])
            if role == "translate":
                return SimpleNamespace(
                    content=json.dumps(
                        {"segments": [{"id": s["id"], "text": "Bad grammar."} for s in body["segments"]]}
                    )
                )
            if role == "critic":
                self.critic_calls += 1
                if self.critic_calls > 1:
                    return SimpleNamespace(content=json.dumps({"findings": []}))
                block_id, record = next(iter(body["block_records"].items()))
                block = record["en_editable_prose"]
                return SimpleNamespace(
                    content=json.dumps(
                            {"findings": [{"finding_id": "ignored", "rule_id": "grammar", "severity": "RED", "block_id": block_id, "range": {"start": 0, "end": len(block.encode())}, "atom_ids": [], "message": "grammar", "required_rule": "English grammar", "context": "Bad grammar.", "repair_class": "prose"}]}
                    )
                )
            return SimpleNamespace(
                content=json.dumps(
                    {"finding_id": body["finding_id"], "block_id": body["block_id"], "replacement": "Good grammar."}
                )
            )

    result = translate_ru_to_en_once(
        "Плохая грамматика.\n",
        RepairingClient(),
        file_path="ydb/docs/ru/a.md",
        manifest=MANIFEST,
    )
    assert "Good grammar." in result.text


def test_repair_request_contains_only_the_matching_ru_prose_and_atom_manifest():
    code_block = "```python\nFINAL007_CODE_SECRET\n```"
    config_block = "```yaml\npassword: FINAL007_CONFIG_SECRET\n```"
    include_directive = (
        "{% include [FINAL007_DIRECTIVE_SECRET](../_includes/example.md) %}"
    )
    ru_source = (
        "Плохая грамматика.\n\n"
        f"{code_block}\n\n{config_block}\n\n{include_directive}\n"
    )
    secrets = (
        "FINAL007_CODE_SECRET",
        "FINAL007_CONFIG_SECRET",
        "FINAL007_DIRECTIVE_SECRET",
    )

    class MinimalContextClient:
        def __init__(self):
            self.critic_calls = 0
            self.non_translation_calls = []

        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            body = json.loads(messages[-1]["content"])
            if role == "translate":
                return SimpleNamespace(content=json.dumps({"segments": [
                    {"id": item["id"], "text": "Bad grammar."}
                    for item in body["segments"]
                ]}))
            self.non_translation_calls.append((role, messages, body))
            if role == "critic":
                self.critic_calls += 1
                if self.critic_calls > 1:
                    return SimpleNamespace(content=json.dumps({"findings": []}))
                block_id, record = next(iter(body["block_records"].items()))
                assert record["corresponding_ru_prose"] == "Плохая грамматика."
                return SimpleNamespace(content=json.dumps({"findings": [{
                    "rule_id": "grammar", "severity": "RED", "block_id": block_id,
                        "range": {"start": 0, "end": len(record["en_editable_prose"].encode())},
                    "atom_ids": [], "message": "grammar", "required_rule": "English grammar",
                    "context": "Bad grammar.", "repair_class": "prose",
                }]}))
            return SimpleNamespace(content=json.dumps({
                "finding_id": body["finding_id"], "block_id": body["block_id"],
                "replacement": "Good grammar.",
            }))

    client = MinimalContextClient()
    result = translate_ru_to_en_once(
        ru_source,
        client,
        file_path="ydb/docs/ru/a.md",
        manifest=MANIFEST,
    )

    assert [role for role, _, _ in client.non_translation_calls] == [
        "critic",
        "repair",
        "critic",
    ]
    critic_payloads = [
        body for role, _, body in client.non_translation_calls if role == "critic"
    ]
    repair_payloads = [
        body for role, _, body in client.non_translation_calls if role == "repair"
    ]
    assert len(repair_payloads) == 1

    for payload in critic_payloads:
        assert set(payload) == {"block_records"}
        for record in payload["block_records"].values():
            assert set(record) == {
                "block_id",
                "en_editable_prose",
                "corresponding_ru_prose",
                "allowed_range",
                "atom_manifest",
            }
            assert all(set(atom) == {"id", "sha256"} for atom in record["atom_manifest"])

    repair_payload = repair_payloads[0]
    assert set(repair_payload) == {
        "finding_id",
        "block_id",
        "range",
        "ru_prose",
        "editable_block",
        "atom_manifest",
        "required_rule",
        "context",
    }
    assert repair_payload["ru_prose"] == "Плохая грамматика."
    assert repair_payload["editable_block"] == "Bad grammar."
    assert all(
        set(atom) == {"id", "sha256"} for atom in repair_payload["atom_manifest"]
    )

    for _, messages, _ in client.non_translation_calls:
        for message in messages:
            serialized_message = json.dumps(message, ensure_ascii=False)
            assert all(secret not in serialized_message for secret in secrets)
            assert ru_source not in serialized_message
            assert result.text not in serialized_message

    assert "Good grammar." in result.text
    assert code_block in result.text
    assert config_block in result.text
    assert include_directive in result.text


def test_local_repair_exhausts_two_logical_attempts_without_extra_recritic():
    class ExhaustingRepairClient:
        def __init__(self):
            self.critic_calls = 0
            self.repair_calls = 0

        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            body = json.loads(messages[-1]["content"])
            if role == "translate":
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "segments": [
                                {"id": item["id"], "text": "Bad grammar."}
                                for item in body["segments"]
                            ]
                        }
                    )
                )
            if role == "critic":
                self.critic_calls += 1
                block_id, record = next(iter(body["block_records"].items()))
                block = record["en_editable_prose"]
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "findings": [
                                {
                                    "finding_id": "ignored",
                                    "rule_id": "grammar",
                                    "severity": "RED",
                                    "block_id": block_id,
                                    "range": {"start": 0, "end": len(block.encode())},
                                    "atom_ids": [],
                                    "message": "fix grammar manually",
                                    "required_rule": "English grammar",
                                    "context": block,
                                    "repair_class": "prose",
                                }
                            ]
                        }
                    )
                )
            self.repair_calls += 1
            return SimpleNamespace(content="not json")

    client = ExhaustingRepairClient()
    with pytest.raises(OnePassTranslationError, match="attempts_exhausted"):
        translate_ru_to_en_once(
            "Плохая грамматика.\n",
            client,
            file_path="ydb/docs/ru/a.md",
            manifest=MANIFEST,
        )

    assert client.critic_calls == 1
    assert client.repair_calls == 4


@pytest.mark.parametrize(
    "invalid_case",
    [
        "finding_id",
        "block_id",
        "empty",
        "protected_token",
        "utf8_range",
        "complete_document",
    ],
)
def test_local_repair_parser_rejects_invalid_primary_and_accepts_fallback(
    invalid_case, monkeypatch,
):
    token = "⟦U1⟧" if invalid_case == "protected_token" else ""
    before = f"Bad {token}grammar. Second sentence. Third sentence. Fourth sentence. Fifth sentence. Sixth sentence."
    fallback = before.replace("Bad", "Good", 1)
    invalid = before.replace("Bad", "INVALID", 1)
    if invalid_case == "protected_token":
        invalid = invalid.replace(token, "")
    elif invalid_case == "utf8_range":
        invalid = before.replace("Sixth sentence.", "INVALID outside.")
    elif invalid_case == "complete_document":
        invalid = before.replace("Bad", "INVALID_GLOBAL", 1)
    elif invalid_case == "empty":
        invalid = "   "

    class RepairClient:
        def __init__(self):
            self.critic_calls = 0
            self.repair_models = []
            self.critic_documents = []

        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            body = json.loads(messages[-1]["content"])
            if role == "critic":
                self.critic_calls += 1
                self.critic_documents.append(
                    tuple(
                        record["en_editable_prose"]
                        for record in body["block_records"].values()
                    )
                )
                if self.critic_calls > 1:
                    return SimpleNamespace(content=json.dumps({"findings": []}))
                block_id, record = next(iter(body["block_records"].items()))
                text = record["en_editable_prose"]
                return SimpleNamespace(content=json.dumps({"findings": [{
                    "rule_id": "grammar", "severity": "RED", "block_id": block_id,
                    "range": {"start": 0, "end": len(b"Bad")},
                    "atom_ids": [], "message": "grammar",
                    "required_rule": "English grammar", "context": text,
                    "repair_class": "prose",
                }]}))
            self.repair_models.append(explicit_model)
            payload = {
                "finding_id": body["finding_id"],
                "block_id": body["block_id"],
                "replacement": invalid if explicit_model == "repair-primary" else fallback,
            }
            if invalid_case == "finding_id" and explicit_model == "repair-primary":
                payload["finding_id"] = "wrong"
            if invalid_case == "block_id" and explicit_model == "repair-primary":
                payload["block_id"] = "wrong"
            return SimpleNamespace(content=json.dumps(payload))

    validated_documents = []

    def validate(document, _context):
        validated_documents.append(document)
        if "INVALID_GLOBAL" in document:
            raise OnePassTranslationError("invalid complete document")

    client = RepairClient()
    monkeypatch.setattr(one_pass_module, "validate_complete_document", validate)
    result = run_bounded_local_repair(
        before,
        before,
        client,
        critic_models=MANIFEST.model_policy.critic,
        repair_models=MANIFEST.model_policy.repair,
        validation_context=object(),
    )

    assert client.repair_models == ["repair-primary", "repair-fallback"]
    assert result.text == fallback
    assert invalid not in result.text
    assert client.critic_documents[0] == (before,)
    assert client.critic_documents[1] == (fallback,)
    assert result.repair_calls == 1
    assert result.reports == ()
    if invalid_case == "complete_document":
        assert any("INVALID_GLOBAL" in document for document in validated_documents)
    else:
        assert all(invalid not in document for document in validated_documents)


def test_local_repair_non_unique_target_exhausts_without_insertion(monkeypatch):
    before = "Bad grammar."
    document = f"{before}\n\n{before}\n"

    class NonUniqueClient:
        def __init__(self):
            self.critic_calls = 0
            self.repair_models = []

        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            body = json.loads(messages[-1]["content"])
            if role == "critic":
                self.critic_calls += 1
                block_id = next(iter(body["block_records"]))
                return SimpleNamespace(content=json.dumps({"findings": [{
                    "rule_id": "grammar", "severity": "RED", "block_id": block_id,
                    "range": {"start": 0, "end": len(before.encode())},
                    "atom_ids": [], "message": "grammar",
                    "required_rule": "English grammar", "context": before,
                    "repair_class": "prose",
                }]}))
            self.repair_models.append(explicit_model)
            replacement = (
                "Primary candidate." if explicit_model == "repair-primary"
                else "Fallback candidate."
            )
            return SimpleNamespace(content=json.dumps({
                "finding_id": body["finding_id"],
                "block_id": body["block_id"],
                "replacement": replacement,
            }))

    validated_documents = []
    monkeypatch.setattr(
        one_pass_module,
        "validate_complete_document",
        lambda candidate, _context: validated_documents.append(candidate),
    )
    client = NonUniqueClient()
    result = run_bounded_local_repair(
        document,
        document,
        client,
        critic_models=MANIFEST.model_policy.critic,
        repair_models=MANIFEST.model_policy.repair,
        validation_context=object(),
    )

    assert client.repair_models == [
        "repair-primary", "repair-fallback", "repair-primary", "repair-fallback"
    ]
    assert validated_documents == []
    assert result.text == document
    assert "Primary candidate." not in result.text
    assert "Fallback candidate." not in result.text
    assert client.critic_calls == 1
    assert all(
        attempt["outcome"] != "recritic"
        for report in result.reports
        for attempt in report["attempts"]
    )


def test_complete_document_invalid_primary_advances_to_translation_fallback():
    class Client:
        def __init__(self):
            self.models = []

        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            if role == "critic":
                return SimpleNamespace(content=json.dumps({"findings": []}))
            self.models.append(explicit_model)
            payload = json.loads(messages[-1]["content"])
            text = "- Broken structure" if explicit_model == "translate-primary" else "English text."
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "segments": [
                            {"id": item["id"], "text": text}
                            for item in payload["segments"]
                        ]
                    }
                )
            )

    client = Client()
    result = translate_ru_to_en_once(
        "Русский текст.\n",
        client,
        file_path="ydb/docs/ru/a.md",
        manifest=MANIFEST,
    )
    assert result.text == "English text.\n"
    assert client.models == ["translate-primary", "translate-fallback"]


def test_invalid_primary_cyrillic_anchor_does_not_poison_fallback_context():
    """FINAL008-IMPL-007: freeze validation context only after acceptance."""

    class Client:
        def __init__(self):
            self.translate_models = []
            self.translate_calls = 0

        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            if role == "repair":
                body = json.loads(messages[-1]["content"])
                heading = body.get("english_heading", "")
                slug = (
                    "primary-heading"
                    if "Primary" in heading
                    else "fallback-heading"
                )
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "finding_id": body["finding_id"],
                            "block_id": "heading-anchor",
                            "replacement": slug,
                        }
                    )
                )
            if role == "critic":
                return SimpleNamespace(content=json.dumps({"findings": []}))
            self.translate_models.append(explicit_model)
            self.translate_calls += 1
            payload = json.loads(messages[-1]["content"])
            segments = payload["segments"]
            if self.translate_calls == 1:
                texts = [
                    {
                        "id": item["id"],
                        "text": (
                            "Primary Heading"
                            if index == 0
                            else "- Broken structure"
                        ),
                    }
                    for index, item in enumerate(segments)
                ]
            else:
                texts = [
                    {
                        "id": item["id"],
                        "text": (
                            "Fallback Heading"
                            if index == 0
                            else "English text."
                        ),
                    }
                    for index, item in enumerate(segments)
                ]
            return SimpleNamespace(content=json.dumps({"segments": texts}))

    client = Client()
    result = translate_ru_to_en_once(
        "# Русский {#якорь}\n\nПараграф.\n",
        client,
        file_path="ydb/docs/ru/a.md",
        manifest=MANIFEST,
    )
    assert "Fallback Heading" in result.text
    assert "{#fallback-heading}" in result.text
    assert "English text." in result.text
    assert client.translate_models == [
        "translate-primary",
        "translate-fallback",
    ]
    assert result.validation_context.expected_anchors == ("fallback-heading",)


def test_complete_document_invalid_primary_and_fallback_exhaust():
    class Client:
        def __init__(self):
            self.models = []

        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            self.models.append(explicit_model)
            payload = json.loads(messages[-1]["content"])
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "segments": [
                            {"id": item["id"], "text": "- Broken structure"}
                            for item in payload["segments"]
                        ]
                    }
                )
            )

    client = Client()
    with pytest.raises(OnePassTranslationError):
        translate_ru_to_en_once(
            "Русский текст.\n",
            client,
            file_path="ydb/docs/ru/a.md",
            manifest=MANIFEST,
        )
    assert client.models == ["translate-primary", "translate-fallback"]


def test_base_and_recritic_use_one_validation_context_identity(monkeypatch):
    observed = []
    real_validate = one_pass_module.validate_complete_document

    def validate_spy(text, validation_context):
        observed.append(validation_context)
        return real_validate(text, validation_context)

    monkeypatch.setattr(one_pass_module, "validate_complete_document", validate_spy)
    result = translate_ru_to_en_once(
        "Русский текст.\n",
        RecordingClient(lambda _text: "English text."),
        file_path="ydb/docs/ru/a.md",
        manifest=MANIFEST,
    )
    assert len(observed) >= 2
    assert all(item is result.validation_context for item in observed)


def test_accepted_repair_uses_identical_frozen_validation_context(monkeypatch):
    """Base accept, repair-candidate validate, and post-repair re-critic share one context."""
    observed: list[object] = []
    real_validate = one_pass_module.validate_complete_document

    def validate_spy(text, validation_context):
        observed.append(validation_context)
        return real_validate(text, validation_context)

    monkeypatch.setattr(one_pass_module, "validate_complete_document", validate_spy)

    class RepairingClient:
        def __init__(self):
            self.critic_calls = 0

        def chat_once(self, messages, *, explicit_model, role, **kwargs):
            body = json.loads(messages[-1]["content"])
            if role == "translate":
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "segments": [
                                {"id": item["id"], "text": "Bad grammar."}
                                for item in body["segments"]
                            ]
                        }
                    )
                )
            if role == "critic":
                self.critic_calls += 1
                if self.critic_calls > 1:
                    return SimpleNamespace(content=json.dumps({"findings": []}))
                block_id, record = next(iter(body["block_records"].items()))
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "findings": [
                                {
                                    "finding_id": "f1",
                                    "rule_id": "grammar",
                                    "severity": "RED",
                                    "block_id": block_id,
                                    "range": {
                                        "start": 0,
                                        "end": len(
                                            record["en_editable_prose"].encode()
                                        ),
                                    },
                                    "atom_ids": [],
                                    "message": "grammar",
                                    "required_rule": "English grammar",
                                    "context": "Bad grammar.",
                                    "repair_class": "prose",
                                }
                            ]
                        }
                    )
                )
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "finding_id": body["finding_id"],
                        "block_id": body["block_id"],
                        "replacement": "Good grammar.",
                    }
                )
            )

    result = translate_ru_to_en_once(
        "Плохая грамматика.\n",
        RepairingClient(),
        file_path="ydb/docs/ru/a.md",
        manifest=MANIFEST,
    )
    assert "Good grammar." in result.text
    # At least: base acquire validate, repair insertion validate, post-repair clear.
    assert len(observed) >= 3
    assert all(item is result.validation_context for item in observed)
