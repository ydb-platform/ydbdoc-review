"""Restore markdown links in EN text using the Russian source."""

from __future__ import annotations

import re

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BARE_URL_IN_PARENS = re.compile(
    r"(?<!\])\b([\w\s—–\-]+?)\s*\((https?://[^)]+)\)"
)
_BROKEN_ANCHOR_LINK = re.compile(r"\[#([^]]+)\]\(\)")
_BARE_REL_MD_IN_PARENS = re.compile(
    r"(?<!\])\((\.\./[\w./\-#{}]+(?:\.md)?(?:#[\w.\-{}]+)?)\)"
)


def _is_relative_doc_href(href: str) -> bool:
    h = href.strip()
    return bool(h) and not h.startswith(("#", "http://", "https://", "mailto:"))


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
        if f"]({href})" not in out and _is_relative_doc_href(href):
            out = _wrap_bare_path_in_line(out, href, ru_text)
    out = fix_bare_relative_md_paths_from_ru(ru, out)
    out = fix_bare_urls_in_prose(out)
    out = fix_broken_anchor_links(out)
    if en.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out


def fix_bare_relative_md_paths_from_ru(ru: str, en: str) -> str:
    """
    Wrap ``English phrase (../../path.md)`` when RU had ``[phrase](../../path.md)``.

    Handles relative doc paths in parentheses without markdown brackets.
    """
    out = en
    seen_hrefs: set[str] = set()
    for ru_text, href in _LINK_RE.findall(ru):
        if href in seen_hrefs or not _is_relative_doc_href(href):
            continue
        seen_hrefs.add(href)
        if f"]({href})" in out:
            continue
        path_only = href.split("#", 1)[0]
        for bare in (f"({href})", f"({path_only})") if path_only else (f"({href})",):
            while bare in out and f"]({href})" not in out:
                prev = out
                use_href = href if bare == f"({href})" else path_only
                out = _wrap_bare_path_in_line(out, use_href, ru_text)
                if out == prev:
                    out = _wrap_bare_paren_path_line(out, bare, href, ru_text)
                if out == prev:
                    break
    return out


def _wrap_bare_paren_path_line(
    en: str, bare_paren: str, href: str, ru_text: str
) -> str:
    """One-line wrap for ``text (../doc.md)`` including ``{{ … }}`` in the label."""
    idx = en.find(bare_paren)
    if idx <= 0:
        return en
    line_start = en.rfind("\n", 0, idx) + 1
    line_end = en.find("\n", idx)
    if line_end < 0:
        line_end = len(en)
    line = en[line_start:line_end]
    if f"]({href})" in line or f"]({href.split('#', 1)[0]})" in line:
        return en
    before = line[: idx - line_start].rstrip()
    m = re.search(r"([\w\s{{}}—–\-.'`/]+)$", before)
    label = (m.group(1).strip() if m else "") or ru_text.strip()
    if not label or label.startswith("http"):
        return en
    label_start = line_start + before.rfind(label)
    new_line = (
        en[line_start:label_start] + f"[{label}]({href})" + en[idx + len(bare_paren) : line_end]
    )
    return en[:line_start] + new_line + en[line_end:]


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

    if f"]({href})" not in en and not re.search(r"[а-яА-ЯёЁ]", ru_text):
        for candidate in (ru_text.strip(), ru_text):
            if candidate and candidate in en and f"[{candidate}]" not in en:
                return en.replace(candidate, f"[{candidate}]({href})", 1)

    if _is_relative_doc_href(href) and f"]({href})" not in en:
        bare = f"({href})"
        if bare in en:
            return _wrap_bare_paren_path_line(en, bare, href, ru_text)

    return en


def _wrap_bare_path_in_line(en: str, href: str, ru_text: str) -> str:
    """
    When EN contains a relative path from RU but without ``[text](path)`` markup,
    wrap the preceding phrase on the same line.
    """
    path = href.split("#", 1)[0]
    if not path or path not in en:
        return en
    idx = 0
    while True:
        pos = en.find(path, idx)
        if pos < 0:
            break
        if pos > 0 and en[pos - 1] == "]":
            idx = pos + len(path)
            continue
        line_start = en.rfind("\n", 0, pos) + 1
        line_end = en.find("\n", pos)
        if line_end < 0:
            line_end = len(en)
        line = en[line_start:line_end]
        if f"]({href})" in line or f"]({path})" in line:
            idx = pos + len(path)
            continue
        before_path = line[: pos - line_start].rstrip()
        m = re.search(r"([\w\s—–\-.,()]+)$", before_path)
        label = (m.group(1).strip() if m else "") or ru_text.strip()
        if not label or label.startswith("http"):
            idx = pos + len(path)
            continue
        label_start = line_start + before_path.rfind(label)
        new_line = (
            en[line_start:label_start]
            + f"[{label}]({href})"
            + en[pos + len(path) : line_end]
        )
        return en[:line_start] + new_line + en[line_end:]
    return en
