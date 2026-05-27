"""Restore markdown links in EN text using the Russian source."""

from __future__ import annotations

import re

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
DIPLODOC_T_MACRO = "{#T}"
_BROKEN_LINK_FRAGMENT_RE = re.compile(
    r"\]\[\(|\]\[\[\(|\[{#T}\]\[\(|\]\(#"
)
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


def _href_key(href: str) -> tuple[str, str]:
    base, _, frag = href.partition("#")
    return (base.strip(), frag.strip())


def _line_has_broken_link_markup(line: str) -> bool:
    return bool(_BROKEN_LINK_FRAGMENT_RE.search(line))


def _valid_en_link_label(text: str) -> bool:
    t = text.strip()
    if not t or t in ("(", "[", "]", "{#T}"):
        return False
    if len(t) < 2 and t not in ("EN", "RU"):
        return False
    return not _line_has_broken_link_markup(f"[{t}](x)")


def _line_needs_link_repair(ru_line: str, en_line: str) -> bool:
    ru_links = _LINK_RE.findall(ru_line)
    if not ru_links:
        return False
    if _line_has_broken_link_markup(en_line):
        return True
    en_links = _LINK_RE.findall(en_line)
    if len(en_links) != len(ru_links):
        if not en_links and re.search(r"https?://", en_line):
            return False
        return True
    for (rt, rh), (et, eh) in zip(ru_links, en_links, strict=True):
        if rt == DIPLODOC_T_MACRO and et != DIPLODOC_T_MACRO:
            return True
        if rt != DIPLODOC_T_MACRO and et == DIPLODOC_T_MACRO:
            return True
        if _href_key(rh) != _href_key(eh):
            return True
    return False


def _default_en_link_label(ru_text: str, href: str) -> str:
    if "topology-select" in href:
        return "topology selection"
    if "tls-certificates" in href:
        return "TLS certificates"
    if "#requirements" in href or href.endswith("requirements"):
        return "requirements"
    if "configuration/index" in href:
        return "configuration reference"
    if "embedded-ui" in href:
        return "embedded UI"
    return ru_text


def _label_for_ru_link(
    ru_text: str,
    href: str,
    *,
    en_line: str,
    en_links: list[tuple[str, str]],
    link_index: int,
) -> str:
    if ru_text == DIPLODOC_T_MACRO:
        return DIPLODOC_T_MACRO
    for et, eh in en_links:
        if _href_key(eh) == _href_key(href) and et != DIPLODOC_T_MACRO:
            return et
    if link_index < len(en_links) and en_links[link_index][0] != DIPLODOC_T_MACRO:
        return en_links[link_index][0]
    bracket = en_line.find("[")
    pre = en_line[:bracket] if bracket >= 0 else en_line
    m = re.search(r"\(see\s+([^[(]+?)\s*$", pre, re.IGNORECASE)
    if m and m.group(1).strip():
        return m.group(1).strip().rstrip(",")
    if re.search(r"[\u0400-\u04FF]", ru_text):
        return _default_en_link_label(ru_text, href)
    return ru_text


def _en_prose_before_broken_links(en_line: str) -> str:
    for marker in ("[{#T}]", "][(", "["):
        idx = en_line.find(marker)
        if idx >= 0:
            return en_line[:idx]
    return en_line


def _en_prose_after_broken_links(en_line: str, *, ru_suffix: str) -> str:
    for sep in (")).", ")).", ")."):
        if sep in en_line:
            tail = en_line.split(sep, 1)[1]
            if tail and not re.search(r"[\u0400-\u04FF]", tail):
                return tail
    return "" if re.search(r"[\u0400-\u04FF]", ru_suffix) else ru_suffix


def _rebuild_line_links_from_ru(ru_line: str, en_line: str) -> str:
    """Rebuild EN line link markup from RU structure; keep EN prose between links."""
    ru_matches = list(_LINK_RE.finditer(ru_line))
    if not ru_matches:
        return en_line

    en_links = [
        (t, h) for t, h in _LINK_RE.findall(en_line) if _valid_en_link_label(t)
    ]

    if _line_has_broken_link_markup(en_line) and len(ru_matches) == 1:
        m = ru_matches[0]
        ru_text, href = m.group(1), m.group(2)
        label = _label_for_ru_link(
            ru_text, href, en_line=en_line, en_links=en_links, link_index=0
        )
        prefix = _en_prose_before_broken_links(en_line)
        if re.search(r"[\u0400-\u04FF]", prefix):
            prefix = ""
        suffix = _en_prose_after_broken_links(
            en_line, ru_suffix=ru_line[m.end() :]
        )
        return f"{prefix}[{label}]({href}){suffix}"

    temp = en_line
    for m in reversed(list(_LINK_RE.finditer(en_line))):
        temp = temp[: m.start()] + "\x00" + temp[m.end() :]
    temp = _BROKEN_LINK_FRAGMENT_RE.sub("\x00", temp)
    temp = re.sub(r"\[{#T}\][^\x00\n]*", "\x00", temp)
    en_segments = temp.split("\x00")

    out: list[str] = []
    for i, m in enumerate(ru_matches):
        if i < len(en_segments):
            out.append(en_segments[i])
        ru_text, href = m.group(1), m.group(2)
        label = _label_for_ru_link(
            ru_text, href, en_line=en_line, en_links=en_links, link_index=i
        )
        out.append(f"[{label}]({href})")
    if len(en_segments) > len(ru_matches):
        out.append(en_segments[len(ru_matches)])
    return "".join(out)


def repair_markdown_links_from_ru(ru: str, en: str) -> str:
    """
  Line-aligned link repair: fix broken ``][(`` markup and ``{#T}`` misuse vs RU.

  Does not translate prose — only restores ``[label](href)`` shape from SOURCE.
  """
    ru_lines = ru.splitlines()
    en_lines = en.splitlines()
    n = max(len(ru_lines), len(en_lines))
    out_lines: list[str] = []
    for i in range(n):
        rl = ru_lines[i] if i < len(ru_lines) else ""
        el = en_lines[i] if i < len(en_lines) else ""
        if rl and el and _line_needs_link_repair(rl, el):
            el = _rebuild_line_links_from_ru(rl, el)
        out_lines.append(el)
    result = "\n".join(out_lines)
    if en.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def restore_markdown_links_from_ru(ru: str, en: str) -> str:
    """
    Re-insert ``[text](href)`` from RU when EN translation dropped link markup.

    Handles known patterns and generic ``(href)`` → ``[text](href)`` when RU
    had a proper link with the same URL.
    """
    out = repair_markdown_links_from_ru(ru, en)
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
