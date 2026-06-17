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
    variable_placeholder_drift_only,
)


def test_extract_placeholders_order():
    text = "Use ⟦C1⟧ then ⟦L1⟧"
    assert extract_placeholders(text) == ["⟦C1⟧", "⟦L1⟧"]


def test_placeholders_must_match_multiset():
    assert placeholders_match("⟦C1⟧ x", "⟦C1⟧ y")
    assert not placeholders_match("⟦C1⟧", "⟦C2⟧")
    # Reordering is legitimate translation behavior — multiset compare.
    assert placeholders_match("⟦C1⟧ ⟦L1⟧", "⟦L1⟧ ⟦C1⟧")


def test_placeholders_tolerate_legitimate_reorder():
    # RU: "к таблице ⟦C1⟧ колонку ⟦C2⟧ с типом ⟦C3⟧"
    # EN: "column ⟦C2⟧ with data type ⟦C3⟧ to the ⟦C1⟧ table"
    src = "к таблице ⟦C1⟧ колонку ⟦C2⟧ с типом ⟦C3⟧"
    tgt = "column ⟦C2⟧ with data type ⟦C3⟧ to the ⟦C1⟧ table"
    assert placeholders_match(src, tgt)


def test_placeholders_detect_lost_block():
    assert not placeholders_match("⟦C1⟧ ⟦C2⟧ ⟦C3⟧", "⟦C1⟧ ⟦C2⟧")


def test_placeholders_detect_substitution():
    assert not placeholders_match("⟦C1⟧ ⟦C2⟧", "⟦C1⟧ ⟦L2⟧")


def test_placeholders_detect_duplicated_block():
    assert not placeholders_match("⟦C1⟧ ⟦C2⟧", "⟦C1⟧ ⟦C1⟧")


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


def test_realign_placeholders_passthrough_on_permutation():
    # Permuted markers (same multiset) must NOT be renumbered: that would
    # re-attach markers to the wrong words.
    source = "к таблице ⟦C1⟧ колонку ⟦C2⟧ с типом ⟦C3⟧"
    translated = "column ⟦C2⟧ with data type ⟦C3⟧ to the ⟦C1⟧ table"
    assert realign_placeholders(source, translated) == translated


def test_variable_placeholder_drift_only():
    assert variable_placeholder_drift_only("⟦V1⟧ a ⟦V2⟧", "⟦V1⟧ a")
    assert not variable_placeholder_drift_only("⟦V1⟧ a ⟦C1⟧", "⟦V1⟧ a")
