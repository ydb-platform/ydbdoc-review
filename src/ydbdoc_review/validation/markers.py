"""Placeholder parity checks for translated segment text."""

from __future__ import annotations

import re
from urllib.parse import unquote

PLACEHOLDER_RE = re.compile(r"⟦[CLIHVTUS]\d+⟧")
# Capturing split keeps marker tokens in the parts list.
PLACEHOLDER_SPLIT_RE = re.compile(r"(⟦[CLIHVTUS]\d+⟧)")
ENCODED_PLACEHOLDER_RE = re.compile(
    r"%e2%9f%a6(?:C|L|I|H|V|T|U|S)\d+%e2%9f%a7",
    re.IGNORECASE,
)


def canonicalize_placeholders(text: str) -> str:
    """Decode URL-encoded protect markers before validation or repair.

    Markdown parsers legitimately percent-encode ``⟦U1⟧`` when it appears in
    a destination. Treating that spelling as prose hid the atom from parity
    checks and allowed it to reach published EN (#51797).
    """
    return ENCODED_PLACEHOLDER_RE.sub(lambda match: unquote(match.group(0)), text)


def extract_placeholders(text: str) -> list[str]:
    """Return placeholder markers in left-to-right order."""
    return PLACEHOLDER_RE.findall(canonicalize_placeholders(text))


def is_placeholder_only_text(text: str) -> bool:
    """True when *text* is only protect markers (and whitespace).

    Config-table key cells are often a single ``⟦C1⟧``. The LLM must not
    expand them into prose that still contains the marker (#48785
    ``default_group`` key became a full sentence).
    """
    if not text or not extract_placeholders(text):
        return False
    remainder = PLACEHOLDER_RE.sub("", text)
    return remainder.strip() == ""


def _link_boundary_sequence_valid(source: str, translated: str) -> bool:
    source_links = [marker for marker in extract_placeholders(source) if marker[1] == "L"]
    translated_links = [
        marker for marker in extract_placeholders(translated) if marker[1] == "L"
    ]
    if not source_links and not translated_links:
        return True
    if translated_links != source_links:
        return False
    for marker in set(source_links):
        if source_links.count(marker) != 2:
            return False
        first = source_links.index(marker)
        if first + 1 >= len(source_links) or source_links[first + 1] != marker:
            return False
    return True


def placeholders_match(source: str, translated: str) -> bool:
    """True when source and translated share the same placeholder multiset.

    Order-insensitive: legitimate translation may reorder inline atoms
    (e.g. RU "к таблице ⟦C1⟧ колонку ⟦C2⟧" → EN "column ⟦C2⟧ to ⟦C1⟧ table").
    Still catches lost, duplicated, or substituted blocks via count parity.
    """
    if not _link_boundary_sequence_valid(source, translated):
        return False
    return sorted(extract_placeholders(source)) == sorted(extract_placeholders(translated))


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
    if sorted(non_variable_placeholders(source)) != sorted(non_variable_placeholders(translated)):
        return False
    return (
        abs(variable_placeholder_count(source) - variable_placeholder_count(translated))
        <= max_v_delta
    )


def cross_lang_placeholder_drift_only(source: str, translated: str) -> bool:
    """True when non-``⟦V⟧`` placeholders match as a multiset but order/ids differ.

    After ``normalize_target_segments_to_source`` (§6.55) the critic should not
    treat renumberings or word-order shifts as corruption when the atom multiset
    is unchanged.
    """
    if extract_placeholders(source) == extract_placeholders(translated):
        return False
    return sorted(non_variable_placeholders(source)) == sorted(
        non_variable_placeholders(translated)
    )


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
    if sorted(src_ph) == sorted(tgt_ph):
        # Same multiset, different order — legitimate translation reorder.
        # Renumbering by source order would re-attach markers to the wrong words.
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
