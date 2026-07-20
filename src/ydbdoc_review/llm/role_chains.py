"""Translate / critic model chains must stay disjoint (§6.127)."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from ydbdoc_review.llm.errors import LLMConfigError

logger = logging.getLogger(__name__)


def _dedupe(models: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for model in models:
        slug = model.strip()
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def ensure_disjoint_translate_critic_chains(
    translate: Sequence[str],
    critic: Sequence[str],
) -> tuple[list[str], list[str]]:
    """Keep translator and critic on different models.

    Primaries must differ. Fallbacks that appear in the other role's chain are
    dropped so a rate-limit failover cannot put both roles on the same slug.
    """
    translate_chain = _dedupe(translate)
    critic_chain = _dedupe(critic)
    if not translate_chain:
        raise LLMConfigError("translate model chain is empty")
    if not critic_chain:
        raise LLMConfigError("critic model chain is empty")

    t_primary = translate_chain[0]
    c_primary = critic_chain[0]
    if t_primary == c_primary:
        raise LLMConfigError(
            "translate and critic must use different primary models; "
            f"both are {t_primary!r}"
        )

    t_set = set(translate_chain)
    c_set = set(critic_chain)
    overlap = t_set & c_set
    translate_out = [t_primary] + [m for m in translate_chain[1:] if m not in c_set]
    critic_out = [c_primary] + [m for m in critic_chain[1:] if m not in t_set]

    if not translate_out:
        raise LLMConfigError(
            "translate model chain is empty after removing models shared with critic; "
            f"translate={list(translate_chain)}, critic={list(critic_chain)}"
        )
    if not critic_out:
        raise LLMConfigError(
            "critic model chain is empty after removing models shared with translate; "
            f"translate={list(translate_chain)}, critic={list(critic_chain)}"
        )
    if overlap:
        logger.warning(
            "Stripped shared translate/critic models so roles stay distinct (§6.127): %s",
            sorted(overlap),
        )
    return translate_out, critic_out
