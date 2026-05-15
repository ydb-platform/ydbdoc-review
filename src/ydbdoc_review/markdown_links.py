"""Restore markdown links in EN text using the Russian source."""

from __future__ import annotations

import re

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def restore_markdown_links_from_ru(ru: str, en: str) -> str:
    """
    Re-insert ``[text](href)`` from RU when diff-based EN translation dropped them.

    Diff mode keeps unchanged EN lines verbatim; new RU sentences with links often
    become plain text in EN (e.g. cross-links to ``create-resource-pool.md#run-access``).
    """
    ru_lines = ru.replace("\r\n", "\n").split("\n")
    en_lines = en.replace("\r\n", "\n").split("\n")
    for i in range(min(len(ru_lines), len(en_lines))):
        for _ru_text, href in _LINK_RE.findall(ru_lines[i]):
            if f"]({href})" in en_lines[i]:
                continue
            en_lines[i] = _inject_link_for_href(ru_lines[i], en_lines[i], href)

    en_text = "\n".join(en_lines)
    if en.endswith("\n") and not en_text.endswith("\n"):
        en_text += "\n"

    for ru_text, href in _LINK_RE.findall(ru):
        if f"]({href})" in en_text:
            continue
        en_text = _inject_link_global(en_text, ru_text, href)

    if en.endswith("\n") and not en_text.endswith("\n"):
        en_text += "\n"
    return en_text


def _inject_link_global(en: str, ru_text: str, href: str) -> str:
    if "#run-access" in href:
        for phrase in (
            "access to the resource pool",
            "Access to the resource pool",
        ):
            if phrase in en and f"[{phrase}]" not in en:
                return en.replace(phrase, f"[{phrase}]({href})", 1)
    return _inject_link_for_href(ru_text, en, href)


def _inject_link_for_href(ru_line: str, en_line: str, href: str) -> str:
    if f"]({href})" in en_line:
        return en_line

    ru_links = _LINK_RE.findall(ru_line)
    ru_texts = [t for t, h in ru_links if h == href]
    if not ru_texts:
        return _inject_link_global(en_line, "", href)

    ru_text = ru_texts[0]
    if "(ACL)" in ru_text and "(ACL)" in en_line and f"]({href})" not in en_line:
        for phrase in (
            "Access control (ACL)",
            "access control (ACL)",
        ):
            if phrase in en_line and f"[{phrase}]" not in en_line:
                return en_line.replace(phrase, f"[{phrase}]({href})", 1)

    if href in en_line and f"]({href})" not in en_line:
        return en_line

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
