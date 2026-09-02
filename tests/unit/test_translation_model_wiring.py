from __future__ import annotations

import ast
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.errors import (
    ChatOnceFailureKind,
    LLMConfigError,
    LLMModelUnavailableError,
    LLMProtocolResponseError,
    LLMRetryableRequestError,
)
from ydbdoc_review.translation.acquisition import (
    AcquisitionBlockedError,
    AcquisitionController,
    AcquisitionExhaustedError,
    AcquisitionProtocolError,
)
from ydbdoc_review.translation.model_policy import (
    ModelPair,
    TranslationJobManifest,
    load_serialized_translation_model_policy,
    load_translation_model_policy,
)
from ydbdoc_review.translation.one_pass import translate_ru_to_en_once


def _raw_policy() -> dict[str, object]:
    return {
        "translate": {"primary": "t1", "fallback": "t2"},
        "critic": {"primary": "c1", "fallback": "c2"},
        "repair": {"primary": "r1", "fallback": "r2"},
    }


def test_manifest_freezes_exact_six_slugs() -> None:
    manifest = TranslationJobManifest(load_translation_model_policy(_raw_policy()))
    assert manifest.model_policy.translate == ModelPair("t1", "t2")
    assert manifest.model_policy.critic == ModelPair("c1", "c2")
    assert manifest.model_policy.repair == ModelPair("r1", "r2")
    with pytest.raises(FrozenInstanceError):
        manifest.model_policy.translate.primary = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.pop("repair"),
        lambda raw: raw.update({"alias": {}}),
        lambda raw: raw["translate"].update({"fallback": "t1"}),
        lambda raw: raw["critic"].update({"primary": ""}),
        lambda raw: raw["repair"].update({"fallback": ["r2"]}),
        lambda raw: raw["translate"].update({"extra": "t3"}),
    ],
)
def test_policy_rejects_every_non_exact_shape(mutate) -> None:
    raw = _raw_policy()
    mutate(raw)
    with pytest.raises(LLMConfigError):
        load_translation_model_policy(raw)


def test_serialized_namespace_is_translation_local_and_shared_config_still_loads(
    tmp_path: Path,
) -> None:
    default_path = Path(__file__).parents[2] / "src/ydbdoc_review/config/default.yaml"
    serialized = default_path.read_text(encoding="utf-8")
    custom = tmp_path / "config.yaml"
    custom.write_text(
        serialized.replace(
            "translate: {primary: deepseek-v32, fallback: yandexgpt-5-pro}",
            "translate: {primary: translation-only, fallback: yandexgpt-5-pro}",
        ),
        encoding="utf-8",
    )

    policy = load_serialized_translation_model_policy(custom)
    shared = load_config(yaml_path=custom, env={})

    assert policy.translate.primary == "translation-only"
    assert shared.llm.models.translate.primary == "deepseek-v32"


class _Client:
    def __init__(self, effects: list[object]) -> None:
        self.effects = list(effects)
        self.calls: list[tuple[str, str, bytes]] = []

    def chat_once(self, messages, *, explicit_model, role, temperature=None, max_tokens=None):
        self.calls.append(
            (
                explicit_model,
                role,
                json.dumps(messages, sort_keys=True, separators=(",", ":")).encode(),
            )
        )
        effect = self.effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return SimpleNamespace(content=effect)


def _controller(client: _Client, role="translate") -> AcquisitionController[str]:
    def parser(response: object) -> str:
        content = response.content  # type: ignore[attr-defined]
        if content == "invalid":
            raise AcquisitionProtocolError("invalid")
        return content

    return AcquisitionController(
        client,
        ModelPair(f"{role}-primary", f"{role}-fallback"),
        role=role,
        parser=parser,
    )


def test_transport_retries_same_slug_then_uses_fallback_and_payload_is_immutable() -> None:
    client = _Client(
        [
            LLMRetryableRequestError("timeout"),
            LLMRetryableRequestError("timeout"),
            "accepted",
        ]
    )
    messages = [{"role": "user", "content": "unchanged"}]
    result = _controller(client).acquire(messages)
    messages[0]["content"] = "mutated later"
    assert [call[:2] for call in client.calls] == [
        ("translate-primary", "translate"),
        ("translate-primary", "translate"),
        ("translate-fallback", "translate"),
    ]
    assert len({call[2] for call in client.calls}) == 1
    assert result.payload == "accepted"


def test_protocol_invalid_and_unavailable_advance_without_same_model_retry() -> None:
    client = _Client(
        [
            "invalid",
            LLMModelUnavailableError("gone"),
        ]
    )
    with pytest.raises(AcquisitionExhaustedError):
        _controller(client).acquire([{"role": "user", "content": "x"}])
    assert [slug for slug, _role, _payload in client.calls] == [
        "translate-primary",
        "translate-fallback",
    ]


def test_provider_protocol_kind_advances_and_four_calls_is_hard_maximum() -> None:
    protocol = LLMProtocolResponseError("empty")
    assert protocol.chat_once_kind is ChatOnceFailureKind.PROTOCOL_INVALID
    client = _Client(
        [
            LLMRetryableRequestError("one"),
            LLMRetryableRequestError("two"),
            LLMRetryableRequestError("three"),
            LLMRetryableRequestError("four"),
            "must not be reached",
        ]
    )
    with pytest.raises(AcquisitionExhaustedError):
        _controller(client).acquire([])
    assert len(client.calls) == 4


def test_unknown_and_permanent_fail_closed_without_fallback() -> None:
    for effect in (RuntimeError("unknown"), LLMConfigError("bad slug")):
        client = _Client([effect, "must not be reached"])
        with pytest.raises(AcquisitionBlockedError):
            _controller(client).acquire([])
        assert len(client.calls) == 1


def test_each_role_uses_only_its_exact_pair() -> None:
    for role in ("translate", "critic", "repair"):
        client = _Client(["ok"])
        _controller(client, role).acquire([])
        assert client.calls[0][:2] == (f"{role}-primary", role)


def test_real_one_pass_wiring_uses_translate_then_read_only_critic_pairs() -> None:
    class ProductionSpy(_Client):
        def chat(self, *args, **kwargs):
            raise AssertionError("shared chat is forbidden")

        def model_chain_for_role(self, *args, **kwargs):
            raise AssertionError("shared chain lookup is forbidden")

    translated = json.dumps(
        {"segments": [{"id": "s0001", "text": "English text."}]}
    )
    clean_critic = json.dumps({"findings": []})
    client = ProductionSpy([translated, clean_critic])
    manifest = TranslationJobManifest(load_translation_model_policy(_raw_policy()))

    result = translate_ru_to_en_once(
        "Русский текст.\n",
        client,
        file_path="ydb/docs/ru/page.md",
        manifest=manifest,
    )

    assert "English text." in result.text
    assert [call[:2] for call in client.calls] == [
        ("t1", "translate"),
        ("c1", "critic"),
    ]


def test_production_translation_ast_has_no_shared_or_legacy_edges() -> None:
    root = Path(__file__).parents[2] / "src" / "ydbdoc_review"
    closure = [
        root / "pipeline" / "orchestrator.py",
        root / "pipeline" / "translate_file.py",
        root / "translation" / "one_pass.py",
        root / "translation" / "local_repair.py",
        root / "translation" / "acquisition.py",
        root / "translation" / "model_policy.py",
    ]
    forbidden = {
        "chat",
        "model_chain_for_role",
        "_model_chain_for_role",
        "_eliza_model_chain",
        "_translate_batch_once",
        "_translate_batch_with_model",
        "repair_segment_translation",
        "critic_retranslate",
        "translate_cyrillic_fence_comments_with_client",
        "translate_cyrillic_prose_with_client",
    }
    found: set[str] = set()
    for path in closure:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden:
                found.add(node.id)
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                found.add(node.attr)
            if isinstance(node, ast.ImportFrom):
                found.update(alias.name for alias in node.names if alias.name in forbidden)
    assert found == set()


def test_retained_shared_chain_has_allowlisted_non_translation_callers() -> None:
    root = Path(__file__).parents[2] / "src" / "ydbdoc_review"
    allowlisted = {
        root / "pipeline" / "analyze.py",
        root / "verification" / "critic.py",
    }
    callers: set[Path] = set()
    for path in allowlisted:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.Attribute) and node.attr in {"chat", "model_chain_for_role"}
            for node in ast.walk(tree)
        ):
            callers.add(path)
    assert callers
