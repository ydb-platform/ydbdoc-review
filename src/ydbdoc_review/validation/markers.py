"""Placeholder parity checks for translated segment text."""

from __future__ import annotations

import re

PLACEHOLDER_RE = re.compile(r"⟦[CLIHVT]\d+⟧")


def extract_placeholders(text: str) -> list[str]:
    """Return placeholder markers in left-to-right order."""
    return PLACEHOLDER_RE.findall(text)


def placeholders_match(source: str, translated: str) -> bool:
    """True when translated text has the same placeholder sequence as source."""
    return extract_placeholders(source) == extract_placeholders(translated)
