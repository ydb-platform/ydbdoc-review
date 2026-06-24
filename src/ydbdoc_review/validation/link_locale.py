"""Deterministic locale fixes and QA checks for link/image URLs."""

from __future__ import annotations

import re
from typing import Callable, Iterable
from urllib.parse import unquote, urlparse

from ydbdoc_review.parsing.ast_types import (
    BlockNode,
    BlockQuote,
    BulletList,
    Document,
    InlineImage,
    InlineLink,
    InlineNode,
    ListItem,
    OrderedList,
    Paragraph,
    Table,
    YfmCut,
    YfmIf,
    YfmNote,
    YfmTabs,
)
from ydbdoc_review.validation.wikipedia_links import (
    resolve_wikipedia_href,
    wiki_lang_for_target,
)

_HOST_REPLACEMENTS_EN: tuple[tuple[str, str], ...] = (
    ("ru.wikipedia.org", "en.wikipedia.org"),
    ("www.ru.wikipedia.org", "en.wikipedia.org"),
)

_HOST_REPLACEMENTS_RU: tuple[tuple[str, str], ...] = (
    ("en.wikipedia.org", "ru.wikipedia.org"),
    ("www.en.wikipedia.org", "ru.wikipedia.org"),
)

_PATH_REPLACEMENTS_EN: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)yandex\.cloud/ru/docs"), "yandex.cloud/en/docs"),
    (re.compile(r"(?i)kubernetes\.io/ru/docs"), "kubernetes.io/docs"),
    (re.compile(r"(?i)(/docs/)ru/"), r"\1en/"),
    (re.compile(r"/ydb/docs/ru/"), "/ydb/docs/en/"),
    (re.compile(r"(?i)json-ru\.html"), "index.html"),
)

_PATH_REPLACEMENTS_RU: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)yandex\.cloud/en/docs"), "yandex.cloud/ru/docs"),
    (re.compile(r"(?i)kubernetes\.io/docs/"), "kubernetes.io/ru/docs/"),
    (re.compile(r"(?i)(/docs/)en/"), r"\1ru/"),
    (re.compile(r"/ydb/docs/en/"), "/ydb/docs/ru/"),
    (re.compile(r"(?i)index\.html"), "json-ru.html"),
)

_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")
_HTTP_HREF = re.compile(r"^https?://", re.I)
_RU_LOCALE_IN_HREF = (
    re.compile(r"(?i)ru\.wikipedia\.org"),
    re.compile(r"(?i)yandex\.cloud/ru/docs"),
    re.compile(r"(?i)kubernetes\.io/ru/docs"),
    re.compile(r"/ydb/docs/ru/"),
    re.compile(r"(?i)(?:^|/)docs/ru/"),
)
_EN_LOCALE_HOSTS = (
    re.compile(r"(?i)en\.wikipedia\.org"),
    re.compile(r"(?i)yandex\.cloud/en/docs"),
)
# ydb docs: RU diagrams use ``-rub.svg``; EN mirror uses the same basename without ``-rub``.
_RU_ASSET_SUFFIX_BEFORE_EXT = re.compile(
    r"-rub(?=\.(?:svg|png|jpe?g|gif|webp)$)",
    re.IGNORECASE,
)
_RELATIVE_HREF = re.compile(r"^(?:\.\.?/|[^:/?#]+/)")


def _is_relative_href(href: str) -> bool:
    if not href or href.startswith("#"):
        return False
    if _HTTP_HREF.match(href) or href.startswith("mailto:"):
        return False
    return bool(_RELATIVE_HREF.match(href) or "/" in href and "://" not in href)


def _mirror_relative_asset_path(href: str, *, target_lang: str) -> str:
    """Map locale-specific asset filenames on relative image/link paths."""
    if not _is_relative_href(href):
        return href
    tgt = target_lang.strip().lower()
    if tgt in {"en", "english"}:
        return _RU_ASSET_SUFFIX_BEFORE_EXT.sub("", href)
    return href


def mirror_link_href(href: str, *, target_lang: str = "en") -> str:
    """Apply deterministic URL locale fixes, including Wikipedia langlinks."""
    if not href or href.startswith("#"):
        return href

    wiki = resolve_wikipedia_href(href, target_lang=target_lang)
    if wiki is not None:
        return wiki

    tgt = target_lang.strip().lower()
    if tgt in {"en", "english"}:
        host_repl = _HOST_REPLACEMENTS_EN
        path_repl = _PATH_REPLACEMENTS_EN
    elif tgt in {"ru", "russian"}:
        host_repl = _HOST_REPLACEMENTS_RU
        path_repl = _PATH_REPLACEMENTS_RU
    else:
        return href

    out = href
    for old, new in host_repl:
        out = out.replace(old, new)
    for pattern, repl in path_repl:
        out = pattern.sub(repl, out)
    return _mirror_relative_asset_path(out, target_lang=target_lang)


def _walk_inline(
    nodes: Iterable[InlineNode],
    *,
    target_lang: str,
    on_href: Callable[[str], None] | None = None,
) -> None:
    for node in nodes:
        if isinstance(node, InlineLink):
            if on_href is not None:
                on_href(node.href)
            else:
                node.href = mirror_link_href(node.href, target_lang=target_lang)
        elif isinstance(node, InlineImage):
            if on_href is not None:
                on_href(node.src)
            else:
                node.src = mirror_link_href(node.src, target_lang=target_lang)
        elif hasattr(node, "children") and isinstance(node.children, list):
            _walk_inline(node.children, target_lang=target_lang, on_href=on_href)


def _walk_blocks(
    blocks: Iterable[BlockNode],
    *,
    target_lang: str,
    on_href: Callable[[str], None] | None = None,
) -> None:
    from ydbdoc_review.parsing.ast_types import Heading, TermDefinition

    for block in blocks:
        if isinstance(block, (Paragraph, Heading, TermDefinition)):
            _walk_inline(block.children, target_lang=target_lang, on_href=on_href)
        elif isinstance(block, (BulletList, OrderedList)):
            for item in block.children:
                if isinstance(item, ListItem):
                    for child in item.children:
                        _walk_blocks([child], target_lang=target_lang, on_href=on_href)
        elif isinstance(block, BlockQuote):
            _walk_blocks(block.children, target_lang=target_lang, on_href=on_href)
        elif isinstance(block, Table):
            for cell in block.header.cells:
                _walk_inline(cell.children, target_lang=target_lang, on_href=on_href)
            for row in block.rows:
                for cell in row.cells:
                    _walk_inline(cell.children, target_lang=target_lang, on_href=on_href)
        elif isinstance(block, YfmNote):
            _walk_blocks(block.children, target_lang=target_lang, on_href=on_href)
        elif isinstance(block, YfmTabs):
            for tab in block.children:
                _walk_inline(tab.title, target_lang=target_lang, on_href=on_href)
                _walk_blocks(tab.children, target_lang=target_lang, on_href=on_href)
        elif isinstance(block, YfmCut):
            _walk_blocks(block.children, target_lang=target_lang, on_href=on_href)
        elif isinstance(block, YfmIf):
            for branch in block.branches:
                _walk_blocks(branch.children, target_lang=target_lang, on_href=on_href)


def collect_link_hrefs(doc: Document) -> list[str]:
    """Return HTTP(S) link and image URLs from a parsed markdown document."""
    hrefs: list[str] = []

    def remember(href: str) -> None:
        if href and _HTTP_HREF.match(href):
            hrefs.append(href)

    _walk_blocks(doc.children, target_lang="en", on_href=remember)
    return hrefs


def collect_relative_hrefs(doc: Document) -> list[str]:
    """Return relative link/image paths from a parsed markdown document."""
    hrefs: list[str] = []

    def remember(href: str) -> None:
        if _is_relative_href(href):
            hrefs.append(href)

    _walk_blocks(doc.children, target_lang="en", on_href=remember)
    return hrefs


def _href_has_cyrillic(href: str) -> bool:
    parsed = urlparse(href)
    path_query = f"{parsed.path}?{parsed.query}"
    return bool(_CYRILLIC.search(unquote(path_query)))


def _ru_locale_leftover(href: str) -> bool:
    return any(pattern.search(href) for pattern in _RU_LOCALE_IN_HREF)


def _en_host_ru_slug(href: str) -> bool:
    if not any(pattern.search(href) for pattern in _EN_LOCALE_HOSTS):
        return False
    return _href_has_cyrillic(href)


def _relative_path_has_ru_asset_suffix(href: str) -> bool:
    return bool(_RU_ASSET_SUFFIX_BEFORE_EXT.search(href))


def check_link_locale_in_en(target_text: str, *, target_lang: str = "en") -> list[str]:
    """Flag broken locale pairing in EN markdown URLs (QA heuristic)."""
    if wiki_lang_for_target(target_lang) != "en":
        return []
    from ydbdoc_review.parsing.markdown_parser import parse_markdown

    issues: list[str] = []
    if _has_lang_conditional_external_link(target_text):
        issues.append(
            "link_locale: {% if lang %} conditional around external link URL "
            "(use a single markdown link; locale is fixed by localize_links)"
        )
    doc = parse_markdown(target_text)
    seen: set[str] = set()
    for href in collect_link_hrefs(doc):
        if href in seen:
            continue
        seen.add(href)
        if _ru_locale_leftover(href):
            issues.append(f"link_locale: RU-locale URL in EN document: {href}")
            continue
        if _en_host_ru_slug(href):
            if "wikipedia.org" in href.lower():
                issues.append(
                    "link_locale: en.wikipedia.org uses Russian article slug "
                    f"(use English title): {href}"
                )
            else:
                issues.append(
                    f"link_locale: Cyrillic path on EN-locale URL: {href}"
                )
    for href in collect_relative_hrefs(doc):
        if href in seen:
            continue
        seen.add(href)
        if _relative_path_has_ru_asset_suffix(href):
            issues.append(
                f"link_locale: RU asset suffix in EN relative path: {href}"
            )
    return issues


def localize_links_in_document(doc: Document, *, target_lang: str = "en") -> None:
    """Rewrite link/image URLs in-place for the target document locale."""
    _walk_blocks(doc.children, target_lang=target_lang)


_WIKI_HREF_IN_TEXT = re.compile(
    r"https?://(?:[a-z]{2,3})\.wikipedia\.org/wiki/[^\s\)\]>\"']+",
    re.IGNORECASE,
)

# LLM sometimes emits ``[label]{% if lang == "ru" %}(url){% endif %}{% if lang == "en" %}…``
# instead of a plain markdown link. External URLs are localized by §6.37 — no YFM branches.
_LANG_LINK_BRANCH = re.compile(
    r"\{%\s*if\s+lang\s*==\s*['\"](?P<lang>ru|en)['\"]\s*%\}"
    r"\((?P<url>https?://.+?)\)"
    r"\{%\s*endif\s*%\}",
    re.IGNORECASE,
)
_LANG_CONDITIONAL_AFTER_LINK_LABEL = re.compile(
    r"(?P<label>!?\[[^\]]+\])"
    r"(?P<branches>(?:\{%\s*if\s+lang\s*==\s*['\"](?:ru|en)['\"]\s*%\}"
    r"\(https?://.+?\)\{%\s*endif\s*%\})+)",
    re.IGNORECASE,
)
_LANG_CONDITIONAL_WRAP_LINK = re.compile(
    r"(?:\{%\s*if\s+lang\s*==\s*['\"](?P<lang>ru|en)['\"]\s*%\}"
    r"(?P<link>!?\[[^\]]+\]\([^)]+\))"
    r"\{%\s*endif\s*%\})+",
    re.IGNORECASE,
)
_LANG_LINK_BRANCH_WRAPPED = re.compile(
    r"\{%\s*if\s+lang\s*==\s*['\"](?P<lang>ru|en)['\"]\s*%\}"
    r"(?P<link>!?\[[^\]]+\]\([^)]+\))"
    r"\{%\s*endif\s*%\}",
    re.IGNORECASE,
)
_INLINE_HTTP_LINK = re.compile(
    r"(!?\[[^\]]+\])\((?P<url>https?://[^)]+)\)",
    re.IGNORECASE,
)


def _wiki_lang_key(target_lang: str) -> str:
    wiki = wiki_lang_for_target(target_lang)
    return wiki if wiki is not None else target_lang.strip().lower()


def _pick_url_from_lang_branches(branches_text: str, *, target_lang: str) -> str | None:
    urls: dict[str, str] = {}
    for match in _LANG_LINK_BRANCH.finditer(branches_text):
        urls[match.group("lang").lower()] = match.group("url")
    if not urls:
        return None

    tgt = _wiki_lang_key(target_lang)
    if tgt in urls:
        chosen = urls[tgt]
    elif tgt == "en" and "ru" in urls:
        chosen = urls["ru"]
    elif tgt == "ru" and "en" in urls:
        chosen = urls["en"]
    else:
        chosen = next(iter(urls.values()))
    return mirror_link_href(chosen, target_lang=target_lang)


def strip_lang_conditionals_around_external_links(
    text: str, *, target_lang: str = "en"
) -> str:
    """Collapse ``{% if lang %}`` branches around external link URLs (§6.64)."""

    def _after_label(match: re.Match[str]) -> str:
        url = _pick_url_from_lang_branches(
            match.group("branches"), target_lang=target_lang
        )
        if url is None:
            return match.group(0)
        return f"{match.group('label')}({url})"

    text = _LANG_CONDITIONAL_AFTER_LINK_LABEL.sub(_after_label, text)

    def _wrapped(match: re.Match[str]) -> str:
        block = match.group(0)
        links_by_lang: dict[str, str] = {}
        for part in _LANG_LINK_BRANCH_WRAPPED.finditer(block):
            links_by_lang[part.group("lang").lower()] = part.group("link")

        if not links_by_lang:
            return block

        tgt = _wiki_lang_key(target_lang)
        link_md = links_by_lang.get(tgt)
        if link_md is None:
            if tgt == "en" and "ru" in links_by_lang:
                link_md = links_by_lang["ru"]
            elif tgt == "ru" and "en" in links_by_lang:
                link_md = links_by_lang["en"]
            else:
                link_md = next(iter(links_by_lang.values()))

        inline = _INLINE_HTTP_LINK.match(link_md)
        if inline is None:
            return link_md
        url = mirror_link_href(inline.group("url"), target_lang=target_lang)
        return f"{inline.group(1)}({url})"

    return _LANG_CONDITIONAL_WRAP_LINK.sub(_wrapped, text)


def _has_lang_conditional_external_link(text: str) -> bool:
    return bool(
        _LANG_CONDITIONAL_AFTER_LINK_LABEL.search(text)
        or _LANG_CONDITIONAL_WRAP_LINK.search(text)
    )


def localize_links_in_text(text: str, *, target_lang: str = "en") -> str:
    """Fix Wikipedia (and other locale) URLs in raw markdown after render."""
    text = strip_lang_conditionals_around_external_links(
        text, target_lang=target_lang
    )

    def _replace(match: re.Match[str]) -> str:
        return mirror_link_href(match.group(0), target_lang=target_lang)

    return _WIKI_HREF_IN_TEXT.sub(_replace, text)
