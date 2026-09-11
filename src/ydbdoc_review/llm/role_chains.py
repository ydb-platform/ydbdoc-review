"""Translate / critic model chains must stay disjoint (§6.127)."""

from __future__ import annotations

from collections.abc import Sequence

from ydbdoc_review.llm.errors import LLMConfigError


def _validate_chain(models: Sequence[str], *, role: str) -> list[str]:
    normalized = [model.strip() for model in models]
    if any(not model for model in normalized):
        raise LLMConfigError(f"{role} model chain contains an empty model")
    if len(set(normalized)) != len(normalized):
        raise LLMConfigError(f"{role} model chain contains a duplicate model")
    if not normalized:
        raise LLMConfigError(f"{role} model chain is empty")
    return normalized


def ensure_disjoint_translate_critic_chains(
    translate: Sequence[str],
    critic: Sequence[str],
) -> tuple[list[str], list[str]]:
    """Validate isolated role chains before any provider request is sent.

    Translation requires a distinct fallback. Any overlap between the two
    chains is rejected instead of silently changing the configured contract.
    """
    translate_chain = _validate_chain(translate, role="translate")
    critic_chain = _validate_chain(critic, role="critic")
    if len(translate_chain) < 2:
        raise LLMConfigError(
            "translate model chain requires a distinct fallback model"
        )

    t_primary = translate_chain[0]
    c_primary = critic_chain[0]
    if t_primary == c_primary:
        raise LLMConfigError(
            "translate and critic must use different primary models; "
            f"both are {t_primary!r}"
        )

    overlap = set(translate_chain) & set(critic_chain)
    if overlap:
        raise LLMConfigError(
            "translate and critic model chains overlap; "
            f"shared models={sorted(overlap)}"
        )
    return translate_chain, critic_chain
