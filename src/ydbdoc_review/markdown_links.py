"""Restore markdown links in EN text using the Russian source (bullet-aligned)."""

from __future__ import annotations

import re

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _bullet_line_indices(lines: list[str]) -> list[int]:
    return [i for i, line in enumerate(lines) if line.lstrip().startswith("- ")]


def restore_markdown_links_from_ru(ru: str, en: str) -> str:
    """
    When RU list items contain ``[text](href)`` but the matching EN bullet lost the link,
    re-insert links for recognizable patterns (e.g. ``(ACL)`` → linked ACL phrase).
    """
    ru_lines = ru.replace("\r\n", "\n").split("\n")
    en_lines = en.replace("\r\n", "\n").split("\n")
    ru_idx = _bullet_line_indices(ru_lines)
    en_idx = _bullet_line_indices(en_lines)
    if not ru_idx or not en_idx:
        return en

    for pos in range(min(len(ru_idx), len(en_idx))):
        ri, ei = ru_idx[pos], en_idx[pos]
        ru_line = ru_lines[ri]
        en_line = en_lines[ei]
        for _ru_text, href in _LINK_RE.findall(ru_line):
            if f"]({href})" in en_line or f"]({href}#" in en_line:
                continue
            en_line = _inject_link_for_href(ru_line, en_line, href)
        en_lines[ei] = en_line

    return "\n".join(en_lines) + ("\n" if en.endswith("\n") else "")


def _inject_link_for_href(ru_line: str, en_line: str, href: str) -> str:
    if f"]({href})" in en_line:
        return en_line

    ru_links = _LINK_RE.findall(ru_line)
    ru_texts = [t for t, h in ru_links if h == href]
    if not ru_texts:
        return en_line

    ru_text = ru_texts[0]
    if "(ACL)" in ru_text and "(ACL)" in en_line and f"]({href})" not in en_line:
        for phrase in (
            "Access control (ACL)",
            "access control (ACL)",
        ):
            if phrase in en_line and f"[{phrase}]" not in en_line:
                return en_line.replace(phrase, f"[{phrase}]({href})", 1)

    if href in en_line:
        return en_line

    # Generic: wrap first parenthesized segment shared with RU link text.
    paren_in_ru = re.search(r"\(([^)]+)\)", ru_text)
    if paren_in_ru:
        token = paren_in_ru.group(1)
        if token in en_line:
            idx = en_line.find(token)
            start = en_line.rfind(" ", 0, idx) + 1
            end = idx + len(token)
            phrase = en_line[start:end]
            if phrase and f"[{phrase}]" not in en_line:
                return en_line[:start] + f"[{phrase}]({href})" + en_line[end:]

    return en_line
