"""Tests for validation markers and CLI tokens."""

from __future__ import annotations

from ydbdoc_review.validation.cli_tokens import (
    cli_tokens_preserved,
    extract_cli_tokens,
)
from ydbdoc_review.validation.markers import (
    extract_placeholders,
    placeholders_match,
    realign_placeholders,
)


def test_extract_placeholders_order():
    text = "Use ⟦C1⟧ then ⟦L1⟧"
    assert extract_placeholders(text) == ["⟦C1⟧", "⟦L1⟧"]


def test_placeholders_must_match_order():
    assert placeholders_match("⟦C1⟧ x", "⟦C1⟧ y")
    assert not placeholders_match("⟦C1⟧", "⟦C2⟧")
    assert not placeholders_match("⟦C1⟧ ⟦L1⟧", "⟦L1⟧ ⟦C1⟧")


def test_extract_cli_flags():
    tokens = extract_cli_tokens("Run with --input-framing and $HOME set")
    assert "--input-framing" in tokens
    assert "$HOME" in tokens


def test_cli_tokens_inside_placeholder_ignored():
    # Placeholder masks inline code; flags in source prose must still match.
    assert cli_tokens_preserved("--verbose flag", "--verbose option")
    assert not cli_tokens_preserved("--verbose flag", "verbose option")


def test_realign_placeholders_renumbers():
    source = "See ⟦L1⟧ and ⟦C2⟧"
    translated = "See ⟦L99⟧ and ⟦C1⟧"
    fixed = realign_placeholders(source, translated)
    assert fixed == "See ⟦L1⟧ and ⟦C2⟧"
    assert placeholders_match(source, fixed)


def test_realign_placeholders_count_mismatch_returns_none():
    assert realign_placeholders("⟦C1⟧ ⟦L1⟧", "⟦C1⟧") is None
