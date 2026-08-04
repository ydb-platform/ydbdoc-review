"""Deterministic RU↔EN link / heading-anchor parity (§6.174).

Policy: for a translated docs page, internal hrefs and explicit ``{#id}``
anchors must match the source twin one-to-one. No EN-only fragment remaps
(``#ldap`` must stay ``#ldap``, not become ``#ldap-auth-provider``).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

from ydbdoc_review.validation.autotitle_hrefs import _AUTO_LINK

DocsTextReader = Callable[[str], str | None]

_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_EXPLICIT_ANCHOR = re.compile(r"\{#([^}]+)\}")
_HTTP = re.compile(r"^https?://", re.IGNORECASE)


def _is_internal_href(href: str) -> bool:
    href = href.strip()
    if not href or href.startswith("mailto:") or _HTTP.match(href):
        return False
    # Autotitle / markdown / in-page fragment.
    if href.startswith("#"):
        return True
    path = href.split("#", 1)[0]
    return path.endswith(".md") or path.endswith(".yaml") or path.endswith(".yml")


def collect_internal_hrefs(text: str) -> list[str]:
    """Internal docs hrefs in document order (autotitle + ``[]()``)."""
    found: list[str] = []
    for href in _AUTO_LINK.findall(text or ""):
        if _is_internal_href(href):
            found.append(href.strip())
    for match in _MD_LINK.finditer(text or ""):
        label, href = match.group(1), match.group(2).strip()
        if label.strip() == "{#T}":
            continue  # already counted via _AUTO_LINK
        if _is_internal_href(href):
            found.append(href)
    return found


def collect_explicit_anchors(text: str) -> list[str]:
    """Explicit ``{#id}`` ids (headings and rare inline), excluding ``{#T}``."""
    out: list[str] = []
    for match in _EXPLICIT_ANCHOR.finditer(text or ""):
        anchor = match.group(1).strip()
        if not anchor or anchor.upper() == "T":
            continue
        out.append(anchor)
    return out


def check_href_parity(
    source_text: str,
    target_text: str,
    *,
    source_lang: str = "ru",
    target_lang: str = "en",
    ignore_basenames: set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Blocking when EN internal href multiset ≠ RU (§6.174)."""
    if source_lang.lower() not in {"ru", "russian"}:
        return []
    if target_lang.lower() not in {"en", "english"}:
        return []

    src = Counter(collect_internal_hrefs(source_text))
    tgt = Counter(collect_internal_hrefs(target_text))
    if ignore_basenames:
        def _kept(counter: Counter[str]) -> Counter[str]:
            out: Counter[str] = Counter()
            for href, n in counter.items():
                base = PurePosixPath(href.split("#", 1)[0]).name
                if base in ignore_basenames:
                    continue
                out[href] = n
            return out

        src = _kept(src)
        tgt = _kept(tgt)

    if src == tgt:
        return []

    missing = sorted((src - tgt).elements())
    extra = sorted((tgt - src).elements())
    parts: list[str] = []
    if missing:
        preview = ", ".join(f"`{h}`" for h in missing[:6])
        more = f", … (+{len(missing) - 6})" if len(missing) > 6 else ""
        parts.append(f"missing in EN: {preview}{more}")
    if extra:
        preview = ", ".join(f"`{h}`" for h in extra[:6])
        more = f", … (+{len(extra) - 6})" if len(extra) > 6 else ""
        parts.append(f"extra in EN: {preview}{more}")
    return [f"href_parity: RU/EN internal links differ — {'; '.join(parts)}"]


def check_heading_anchor_parity(
    source_text: str,
    target_text: str,
    *,
    source_lang: str = "ru",
    target_lang: str = "en",
) -> list[str]:
    """Blocking when explicit ``{#id}`` multisets differ (RU vs EN)."""
    if source_lang.lower() not in {"ru", "russian"}:
        return []
    if target_lang.lower() not in {"en", "english"}:
        return []

    src = Counter(collect_explicit_anchors(source_text))
    tgt = Counter(collect_explicit_anchors(target_text))
    if src == tgt:
        return []

    missing = sorted((src - tgt).elements())
    extra = sorted((tgt - src).elements())
    parts: list[str] = []
    if missing:
        preview = ", ".join(f"`{{#{a}}}`" for a in missing[:8])
        more = f", … (+{len(missing) - 8})" if len(missing) > 8 else ""
        parts.append(f"missing in EN: {preview}{more}")
    if extra:
        preview = ", ".join(f"`{{#{a}}}`" for a in extra[:8])
        more = f", … (+{len(extra) - 8})" if len(extra) > 8 else ""
        parts.append(f"extra in EN: {preview}{more}")
    return [
        f"anchor_parity: RU/EN explicit {{#id}} differ — {'; '.join(parts)}"
    ]


def _href_targets_page(href: str, en_page_path: str) -> str | None:
    """Return fragment if ``href`` points at ``en_page_path``, else None."""
    href = href.strip()
    if "#" not in href:
        return None
    path_part, frag = href.rsplit("#", 1)
    if not frag:
        return None
    page_name = PurePosixPath(en_page_path).name
    if path_part in {"", "."}:
        # in-page — not inbound from another file
        return None
    if path_part == page_name or path_part.endswith("/" + page_name):
        return frag
    # Absolute-from-docs style rarely used; also accept full repo-relative match.
    if path_part == en_page_path or path_part.endswith(en_page_path):
        return frag
    return None


def iter_en_markdown_paths(repo_path: str, *, docs_root: str = "ydb/docs") -> list[str]:
    root = Path(repo_path) / docs_root / "en"
    if not root.is_dir():
        return []
    out: list[str] = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(repo_path).as_posix()
        out.append(rel)
    return out


def check_inbound_fragments(
    en_page_path: str,
    en_text: str,
    *,
    repo_path: str | None = None,
    read_text: DocsTextReader | None = None,
    en_paths: Iterable[str] | None = None,
    docs_root: str = "ydb/docs",
) -> list[str]:
    """Blocking when other EN pages link to missing ``#frag`` on this page.

    Catches the #48792 hole: ``authentication.md`` anchors become ``{#ldap}``
    while ``create-resource-pool-classifier.md`` still has ``#ldap-auth-provider``.
    """
    if not en_page_path or not en_text:
        return []
    if en_paths is None:
        if not repo_path:
            return []
        en_paths = iter_en_markdown_paths(repo_path, docs_root=docs_root)

    declared = set(collect_explicit_anchors(en_text))
    # Also treat Diplodoc auto-slugs as declared via fragment_repair helper.
    from ydbdoc_review.validation.fragment_repair import fragment_declared_in_markdown

    issues: list[str] = []
    seen: set[str] = set()
    for other_path in en_paths:
        if other_path == en_page_path:
            continue
        if read_text is not None:
            other_text = read_text(other_path)
        elif repo_path:
            p = Path(repo_path) / other_path
            other_text = p.read_text(encoding="utf-8") if p.is_file() else None
        else:
            other_text = None
        if not other_text:
            continue
        for href in collect_internal_hrefs(other_text):
            frag = _href_targets_page(href, en_page_path)
            if not frag:
                continue
            key = f"{other_path}::{href}"
            if key in seen:
                continue
            seen.add(key)
            ok = frag in declared or fragment_declared_in_markdown(
                en_text, frag, page_path=en_page_path, read_text=read_text
            )
            if ok:
                continue
            issues.append(
                f"inbound_fragment: `{other_path}` links to "
                f"`{PurePosixPath(en_page_path).name}#{frag}` but that anchor "
                f"is missing on the translated page"
            )
            if len(issues) >= 12:
                issues.append("inbound_fragment: … further inbound misses truncated")
                return issues
    return issues
