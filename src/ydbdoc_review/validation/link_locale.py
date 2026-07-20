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
    parse_wikipedia_href,
    resolve_wikipedia_href,
    wiki_lang_for_target,
)
from ydbdoc_review.validation.yfm_anchor import (
    build_heading_anchor_map,
    diplodoc_auto_slug,
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


def _strip_md(text: str) -> str:
    return re.sub(r"[*_`]", "", text).strip()


def _remap_anchor_fragment(
    href: str,
    anchor_map: dict[str, str] | None,
    *,
    link_text: str | None = None,
) -> str:
    """Rewrite ``#fragment`` or ``path#fragment`` using heading anchor map."""
    if not href or href.startswith(("http://", "https://", "mailto:")):
        return href

    def _map_frag(fragment: str) -> str:
        decoded = unquote(fragment)
        if anchor_map:
            if decoded in anchor_map:
                return anchor_map[decoded]
            if fragment in anchor_map:
                return anchor_map[fragment]
        if _CYRILLIC.search(decoded) and link_text:
            slug = diplodoc_auto_slug(_strip_md(link_text))
            if slug:
                return slug
        return fragment

    if href.startswith("#"):
        return f"#{_map_frag(href[1:])}"
    if "#" not in href:
        return href
    base, fragment = href.split("#", 1)
    return f"{base}#{_map_frag(fragment)}"


def mirror_link_href(
    href: str,
    *,
    target_lang: str = "en",
    anchor_map: dict[str, str] | None = None,
    link_text: str | None = None,
) -> str:
    """Apply deterministic URL locale fixes, including Wikipedia langlinks."""
    if not href:
        return href

    tgt = target_lang.strip().lower()
    if tgt in {"en", "english"}:
        href = _remap_anchor_fragment(href, anchor_map, link_text=link_text)

    if href.startswith("#"):
        return href

    wiki = resolve_wikipedia_href(href, target_lang=target_lang)
    if wiki is not None:
        return wiki
    # Unresolved Wikipedia: do not naive-swap ru.↔en. host (keeps Cyrillic
    # slug on the wrong edition). Leave original for link_locale heuristics.
    if parse_wikipedia_href(href) is not None:
        return href

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
    anchor_map: dict[str, str] | None = None,
    on_href: Callable[[str], None] | None = None,
) -> None:
    for node in nodes:
        if isinstance(node, InlineLink):
            if on_href is not None:
                on_href(node.href)
            else:
                from ydbdoc_review.rendering.markdown_renderer import _render_inline

                link_text = _render_inline(node.children)
                node.href = mirror_link_href(
                    node.href,
                    target_lang=target_lang,
                    anchor_map=anchor_map,
                    link_text=link_text,
                )
        elif isinstance(node, InlineImage):
            if on_href is not None:
                on_href(node.src)
            else:
                node.src = mirror_link_href(
                    node.src, target_lang=target_lang, anchor_map=anchor_map
                )
        elif hasattr(node, "children") and isinstance(node.children, list):
            _walk_inline(
                node.children,
                target_lang=target_lang,
                anchor_map=anchor_map,
                on_href=on_href,
            )


def _walk_blocks(
    blocks: Iterable[BlockNode],
    *,
    target_lang: str,
    anchor_map: dict[str, str] | None = None,
    on_href: Callable[[str], None] | None = None,
) -> None:
    from ydbdoc_review.parsing.ast_types import Heading, TermDefinition

    for block in blocks:
        if isinstance(block, (Paragraph, Heading, TermDefinition)):
            _walk_inline(
                block.children,
                target_lang=target_lang,
                anchor_map=anchor_map,
                on_href=on_href,
            )
        elif isinstance(block, (BulletList, OrderedList)):
            for item in block.children:
                if isinstance(item, ListItem):
                    for child in item.children:
                        _walk_blocks(
                            [child],
                            target_lang=target_lang,
                            anchor_map=anchor_map,
                            on_href=on_href,
                        )
        elif isinstance(block, BlockQuote):
            _walk_blocks(
                block.children,
                target_lang=target_lang,
                anchor_map=anchor_map,
                on_href=on_href,
            )
        elif isinstance(block, Table):
            for cell in block.header.cells:
                _walk_inline(
                    cell.children,
                    target_lang=target_lang,
                    anchor_map=anchor_map,
                    on_href=on_href,
                )
            for row in block.rows:
                for cell in row.cells:
                    _walk_inline(
                        cell.children,
                        target_lang=target_lang,
                        anchor_map=anchor_map,
                        on_href=on_href,
                    )
        elif isinstance(block, YfmNote):
            _walk_blocks(
                block.children,
                target_lang=target_lang,
                anchor_map=anchor_map,
                on_href=on_href,
            )
        elif isinstance(block, YfmTabs):
            for tab in block.children:
                _walk_inline(
                    tab.title,
                    target_lang=target_lang,
                    anchor_map=anchor_map,
                    on_href=on_href,
                )
                _walk_blocks(
                    tab.children,
                    target_lang=target_lang,
                    anchor_map=anchor_map,
                    on_href=on_href,
                )
        elif isinstance(block, YfmCut):
            _walk_blocks(
                block.children,
                target_lang=target_lang,
                anchor_map=anchor_map,
                on_href=on_href,
            )
        elif isinstance(block, YfmIf):
            for branch in block.branches:
                _walk_blocks(
                    branch.children,
                    target_lang=target_lang,
                    anchor_map=anchor_map,
                    on_href=on_href,
                )


def collect_link_hrefs(doc: Document) -> list[str]:
    """Return HTTP(S) link and image URLs from a parsed markdown document."""
    hrefs: list[str] = []

    def remember(href: str) -> None:
        if href and _HTTP_HREF.match(href):
            hrefs.append(href)

    _walk_blocks(doc.children, target_lang="en", on_href=remember)
    return hrefs


def collect_fragment_hrefs(doc: Document) -> list[str]:
    """Return in-page and relative hrefs that include a ``#fragment``."""
    hrefs: list[str] = []

    def remember(href: str) -> None:
        if not href or _HTTP_HREF.match(href) or href.startswith("mailto:"):
            return
        if href.startswith("#") or "#" in href:
            hrefs.append(href)

    _walk_blocks(doc.children, target_lang="en", on_href=remember)
    return hrefs


def _fragment_has_cyrillic(href: str) -> bool:
    if href.startswith("#"):
        return bool(_CYRILLIC.search(unquote(href[1:])))
    if "#" in href:
        return bool(_CYRILLIC.search(unquote(href.rsplit("#", 1)[1])))
    return False


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

    doc = parse_markdown(target_text)
    issues: list[str] = []
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
    for href in collect_fragment_hrefs(doc):
        if href in seen:
            continue
        seen.add(href)
        if _fragment_has_cyrillic(href):
            issues.append(
                f"link_locale: Cyrillic anchor fragment in EN document: {href}"
            )
    return issues


def localize_links_in_document(
    doc: Document,
    *,
    target_lang: str = "en",
    source_doc: Document | None = None,
    anchor_map: dict[str, str] | None = None,
) -> None:
    """Rewrite link/image URLs in-place for the target document locale."""
    tgt = target_lang.strip().lower()
    if anchor_map is None and tgt in {"en", "english"} and source_doc is not None:
        anchor_map = build_heading_anchor_map(source_doc, doc)
    _walk_blocks(doc.children, target_lang=target_lang, anchor_map=anchor_map)


_WIKI_HREF_IN_TEXT = re.compile(
    r"https?://(?:[a-z]{2,3})\.wikipedia\.org/wiki/[^\s\)\]>\"']+",
    re.IGNORECASE,
)


def localize_links_in_text(text: str, *, target_lang: str = "en") -> str:
    """Fix Wikipedia (and other locale) URLs in raw markdown after render."""

    def _replace(match: re.Match[str]) -> str:
        return mirror_link_href(match.group(0), target_lang=target_lang)

    return _WIKI_HREF_IN_TEXT.sub(_replace, text)
