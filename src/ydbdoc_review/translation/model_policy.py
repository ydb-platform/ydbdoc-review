"""Immutable, translation-local model selection policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from openai.types.chat import ChatCompletionMessageParam

from ydbdoc_review.llm.client import ChatResult
from ydbdoc_review.llm.errors import LLMConfigError


@dataclass(frozen=True)
class ModelPair:
    primary: str
    fallback: str


@dataclass(frozen=True)
class TranslationModelPolicy:
    translate: ModelPair
    critic: ModelPair
    repair: ModelPair


class TranslationChatOnce(Protocol):
    def chat_once(
        self,
        messages: Sequence[ChatCompletionMessageParam],
        *,
        explicit_model: str,
        role: Literal["translate", "critic", "repair", "navigation"],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult: ...


@dataclass(frozen=True)
class TranslationJobManifest:
    """The six model slugs frozen before source reads or model requests."""

    model_policy: TranslationModelPolicy

    def __post_init__(self) -> None:
        _validate_client_independent_policy(self.model_policy)


_ROLES = ("translate", "critic", "repair")
_PAIR_KEYS = frozenset({"primary", "fallback"})


def _validate_client_independent_policy(policy: TranslationModelPolicy) -> None:
    for role in _ROLES:
        pair = getattr(policy, role)
        if not pair.primary.strip() or not pair.fallback.strip():
            raise LLMConfigError(f"translation_model_policy.{role} slugs must be non-empty")
        if pair.primary != pair.primary.strip() or pair.fallback != pair.fallback.strip():
            raise LLMConfigError(
                f"translation_model_policy.{role} slugs must not contain surrounding whitespace"
            )
        if pair.primary == pair.fallback:
            raise LLMConfigError(
                f"translation_model_policy.{role} requires distinct primary and fallback slugs"
            )


def load_translation_model_policy(
    raw_mapping: Mapping[str, Any],
) -> TranslationModelPolicy:
    """Parse only the strict ``translation_model_policy`` raw subtree."""
    if not isinstance(raw_mapping, Mapping):
        raise LLMConfigError("translation_model_policy must be a mapping")
    keys = frozenset(raw_mapping)
    expected = frozenset(_ROLES)
    if keys != expected:
        missing = sorted(expected - keys)
        unknown = sorted(keys - expected)
        raise LLMConfigError(
            f"translation_model_policy requires exact roles; missing={missing}, unknown={unknown}"
        )

    pairs: dict[str, ModelPair] = {}
    for role in _ROLES:
        raw_pair = raw_mapping[role]
        if not isinstance(raw_pair, Mapping) or frozenset(raw_pair) != _PAIR_KEYS:
            raise LLMConfigError(
                f"translation_model_policy.{role} requires exactly primary and fallback"
            )
        primary = raw_pair["primary"]
        fallback = raw_pair["fallback"]
        if not isinstance(primary, str) or not isinstance(fallback, str):
            raise LLMConfigError(
                f"translation_model_policy.{role} slugs must be strings"
            )
        pairs[role] = ModelPair(primary=primary, fallback=fallback)

    policy = TranslationModelPolicy(
        translate=pairs["translate"],
        critic=pairs["critic"],
        repair=pairs["repair"],
    )
    _validate_client_independent_policy(policy)
    return policy


def load_serialized_translation_model_policy(
    yaml_path: Path | None = None,
) -> TranslationModelPolicy:
    """Read the dedicated namespace without involving shared Config models."""
    if yaml_path is None:
        package = resources.files("ydbdoc_review.config")
        text = (package / "default.yaml").read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
    else:
        with yaml_path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream) or {}
    if not isinstance(raw, Mapping) or "translation_model_policy" not in raw:
        raise LLMConfigError("missing translation_model_policy namespace")
    return load_translation_model_policy(raw["translation_model_policy"])


def freeze_translation_job_manifest(
    raw_mapping: Mapping[str, Any],
) -> TranslationJobManifest:
    return TranslationJobManifest(load_translation_model_policy(raw_mapping))


def require_translation_chat_once(client: object) -> TranslationChatOnce:
    """Fail closed at bootstrap when the approved primitive is unavailable."""
    if not callable(getattr(client, "chat_once", None)):
        raise LLMConfigError("translation client lacks required chat_once capability")
    return client  # type: ignore[return-value]
