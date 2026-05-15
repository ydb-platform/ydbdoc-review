"""Restore markdown links in EN text using the Russian source."""

from __future__ import annotations

import re

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def restore_markdown_links_from_ru(ru: str, en: str) -> str:
    """
    Re-insert ``[text](href)`` from RU when diff-based EN translation dropped them.

    Only handles a small set of known patterns (no recursion, no line alignment).
    """
    out = en
    for ru_text, href in _LINK_RE.findall(ru):
        if f"]({href})" in out:
            continue
        out = _restore_one_link(ru_text, out, href)
    if en.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out


def _restore_one_link(ru_text: str, en: str, href: str) -> str:
    if "#run-access" in href:
        for phrase in (
            "access to the resource pool",
            "Access to the resource pool",
        ):
            if phrase in en and f"[{phrase}]" not in en:
                return en.replace(phrase, f"[{phrase}]({href})", 1)

    if "(ACL)" in ru_text:
        for phrase in (
            "Access control (ACL)",
            "access control (ACL)",
        ):
            if phrase in en and f"[{phrase}]" not in en:
                return en.replace(phrase, f"[{phrase}]({href})", 1)

    return en
