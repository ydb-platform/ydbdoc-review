"""Restore markdown links in EN text using the Russian source."""

from __future__ import annotations

import re

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BARE_URL_IN_PARENS = re.compile(
    r"(?<!\])\b([\w\s—–\-]+?)\s*\((https?://[^)]+)\)"
)
_BROKEN_ANCHOR_LINK = re.compile(r"\[#([^]]+)\]\(\)")


def restore_markdown_links_from_ru(ru: str, en: str) -> str:
    """
    Re-insert ``[text](href)`` from RU when EN translation dropped link markup.

    Handles known patterns and generic ``(href)`` → ``[text](href)`` when RU
    had a proper link with the same URL.
    """
    out = en
    for ru_text, href in _LINK_RE.findall(ru):
        if f"]({href})" in out:
            continue
        out = _restore_one_link(ru_text, out, href)
        if f"]({href})" not in out and f"({href})" in out:
            out = _wrap_bare_url_with_href(out, href)
    out = fix_bare_urls_in_prose(out)
    out = fix_broken_anchor_links(out)
    if en.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out


def _wrap_bare_url_with_href(en: str, href: str) -> str:
    """Turn ``…text (href)`` into ``…[text](href)`` when href is known from RU."""
    bare = f"({href})"
    idx = en.find(bare)
    if idx <= 0:
        return en
    before = en[:idx].rstrip()
    # Take trailing phrase before the bare URL (up to ~120 chars).
    chunk = before[max(0, len(before) - 120) :]
    m = re.search(r"([\w\s—–\-]+)$", chunk)
    if not m:
        return en
    text = m.group(1).strip()
    if not text or text.startswith("http"):
        return en
    start = before.rfind(text)
    if start < 0:
        return en
    return en[:start] + f"[{text}]({href})" + en[idx + len(bare) :]


def fix_bare_urls_in_prose(en: str) -> str:
    """Wrap ``wordy text (https://...)`` in markdown link brackets."""

    def repl(m: re.Match[str]) -> str:
        text = m.group(1).strip()
        url = m.group(2)
        if not text or text.startswith("http"):
            return m.group(0)
        low = text.lower()
        if low.startswith("limited ") and "lifespan" in low:
            text = text[len("limited ") :].strip()
        return f"[{text}]({url})"

    return _BARE_URL_IN_PARENS.sub(repl, en)


def fix_broken_anchor_links(en: str) -> str:
    """``[#rag]()`` → ``[RAG](#rag)`` (label from anchor)."""

    def repl(m: re.Match[str]) -> str:
        anchor = m.group(1)
        label = anchor.upper() if anchor.islower() and len(anchor) <= 8 else anchor
        return f"[{label}](#{anchor})"

    return _BROKEN_ANCHOR_LINK.sub(repl, en)


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

    if "срок жизни" in ru_text or "#lifetime" in href:
        m = re.search(
            r"(limited\s+)?(lifespan — no more than 12 hours)\s*"
            r"\((https?://[^)]+#lifetime)\)",
            en,
            re.IGNORECASE,
        )
        if m and f"[{m.group(2)}]" not in en:
            return en.replace(m.group(0), f"[{m.group(2)}]({m.group(3)})", 1)

    if "RAG" in ru_text and "#rag" in href:
        if "requires RAG enabled" in en and "[RAG](#rag)" not in en:
            return en.replace("requires RAG enabled", "requires [RAG](#rag) enabled", 1)

    return en
