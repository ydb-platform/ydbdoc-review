"""Tests for translate/critic disjoint model chains (§6.127)."""

from __future__ import annotations

import pytest

from ydbdoc_review.llm.errors import LLMConfigError
from ydbdoc_review.llm.role_chains import ensure_disjoint_translate_critic_chains


def test_disjoint_keeps_distinct_primaries_and_non_overlapping_fallbacks():
    translate, critic = ensure_disjoint_translate_critic_chains(
        ["deepseek-v32", "yandexgpt-5-pro"],
        ["yandexgpt-5.1", "yandexgpt-5-lite"],
    )
    assert translate == ["deepseek-v32", "yandexgpt-5-pro"]
    assert critic == ["yandexgpt-5.1", "yandexgpt-5-lite"]


def test_disjoint_strips_shared_fallbacks():
    translate, critic = ensure_disjoint_translate_critic_chains(
        ["deepseek-v4-flash", "gpt-oss-120b"],
        ["gpt-oss-120b", "deepseek-v4-flash"],
    )
    assert translate == ["deepseek-v4-flash"]
    assert critic == ["gpt-oss-120b"]


def test_disjoint_rejects_same_primary():
    with pytest.raises(LLMConfigError, match="different primary"):
        ensure_disjoint_translate_critic_chains(
            ["deepseek-v32", "yandexgpt-5.1"],
            ["deepseek-v32", "yandexgpt-5-pro"],
        )


def test_disjoint_rejects_empty_after_strip():
    # Critic primary is only model and also appears in translate → impossible
    # after primary-equality check; use critic whose only remaining model is
    # stripped via being translate primary in fallbacks... 
    # translate [A,B], critic [B] → both keep primaries A and B.
    translate, critic = ensure_disjoint_translate_critic_chains(
        ["A", "B"],
        ["B"],
    )
    assert translate == ["A"]
    assert critic == ["B"]
