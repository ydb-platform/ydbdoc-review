"""Placeholder parity checks for translated segment text."""

from __future__ import annotations

import re

PLACEHOLDER_RE = re.compile(r"⟦[CLIHVTUS]\d+⟧")
# Capturing split keeps marker tokens in the parts list.
PLACEHOLDER_SPLIT_RE = re.compile(r"(⟦[CLIHVTUS]\d+⟧)")


def extract_placeholders(text: str) -> list[str]:
    """Return placeholder markers in left-to-right order."""
    return PLACEHOLDER_RE.findall(text)


def placeholders_match(source: str, translated: str) -> bool:
    """True when translated text has the same placeholder sequence as source."""
    return extract_placeholders(source) == extract_placeholders(translated)


def non_variable_placeholders(text: str) -> list[str]:
    """Placeholders other than ``⟦V{n}⟧`` (code, URLs, images, …)."""
    return [p for p in extract_placeholders(text) if p[1] != "V"]


def variable_placeholder_count(text: str) -> int:
    return sum(1 for p in extract_placeholders(text) if p[1] == "V")


def variable_placeholder_drift_only(
    source: str,
    translated: str,
    *,
    max_v_delta: int = 1,
) -> bool:
    """True when RU/EN differ only in ``⟦V⟧`` count (human ``{{ var }}`` placement)."""
    if placeholders_match(source, translated):
        return False
    if non_variable_placeholders(source) != non_variable_placeholders(translated):
        return False
    return abs(variable_placeholder_count(source) - variable_placeholder_count(translated)) <= max_v_delta


def realign_placeholders(source: str, translated: str) -> str | None:
    """Fix renumbered placeholders in *translated* using *source* sequence.

    LLMs often preserve placeholder count but change indices (⟦C1⟧ → ⟦C2⟧).
    When counts match, rebuild *translated* with source markers and the same
    prose fragments. Returns *translated* unchanged when already aligned,
    a fixed string when realigned, or ``None`` when counts differ.
    """
    src_ph = extract_placeholders(source)
    tgt_ph = extract_placeholders(translated)
    if src_ph == tgt_ph:
        return translated
    if len(src_ph) != len(tgt_ph):
        return None
    parts = PLACEHOLDER_SPLIT_RE.split(translated)
    if len(parts) != len(src_ph) * 2 + 1:
        return None
    rebuilt = parts[0]
    for i, ph in enumerate(src_ph):
        rebuilt += ph + parts[2 * i + 2]
    return rebuilt
