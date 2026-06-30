"""YFM heading anchors: parse Cyrillic ids and emit English anchors for EN docs."""

from __future__ import annotations

import re

_HEADING_ANCHOR_SUFFIX = re.compile(r"\s*\{#([^}]+)\}\s*$")


def split_heading_anchor_suffix(text: str) -> tuple[str, str | None]:
    """Split trailing ``{#anchor}`` from heading inline text."""
    match = _HEADING_ANCHOR_SUFFIX.search(text)
    if not match:
        return text, None
    return text[: match.start()].rstrip(), match.group(1)


def english_yfm_anchor(ru_anchor: str | None, english_heading: str) -> str | None:
    """Map a RU/Cyrillic YFM anchor to an English id for EN output.

  Examples: ``fields-Описание`` + "Description of fields…" → ``fields-Description``.
  ASCII anchors are returned unchanged.
    """
    if not ru_anchor:
        return None
    if ru_anchor.isascii() and re.fullmatch(r"[A-Za-z0-9_\-.]+", ru_anchor):
        return ru_anchor

    prefix, sep, suffix = ru_anchor.partition("-")
    if sep and suffix and not suffix.isascii():
        word = re.match(r"([A-Za-z][A-Za-z0-9]*)", english_heading.strip())
        if word:
            return f"{prefix}-{word.group(1)}"

    slug = re.sub(r"[^\w\s-]", "", english_heading).strip().lower()
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug or ru_anchor
