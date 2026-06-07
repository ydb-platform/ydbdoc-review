"""Resolve Wikipedia article titles via the MediaWiki API (no LLM)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse, urlunparse

import requests

logger = logging.getLogger(__name__)

_WIKI_PATH = "/wiki/"
_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")
_TARGET_TO_WIKI_LANG = {
    "en": "en",
    "english": "en",
    "ru": "ru",
    "russian": "ru",
}

@dataclass
class WikipediaResolver:
    """Cached langlink lookups between Wikipedia language editions."""

    timeout_s: float = 10.0
    _cache: dict[tuple[str, str, str], str | None] = field(default_factory=dict)

    def resolve_title(
        self,
        source_lang: str,
        title: str,
        target_lang: str,
    ) -> str | None:
        source_lang = source_lang.lower()
        target_lang = target_lang.lower()
        if source_lang == target_lang:
            return title
        key = (source_lang, title, target_lang)
        if key not in self._cache:
            self._cache[key] = self._fetch_langlink(source_lang, title, target_lang)
        return self._cache[key]

    def _fetch_langlink(
        self,
        source_lang: str,
        title: str,
        target_lang: str,
    ) -> str | None:
        url = f"https://{source_lang}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "titles": title,
            "prop": "langlinks",
            "lllang": target_lang,
            "format": "json",
            "formatversion": "2",
        }
        try:
            resp = requests.get(url, params=params, timeout=self.timeout_s)
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", [])
            if not pages:
                return None
            page = pages[0]
            if page.get("missing"):
                return None
            for link in page.get("langlinks") or []:
                if str(link.get("lang", "")).lower() == target_lang:
                    resolved = str(link.get("title") or "").strip()
                    return resolved or None
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Wikipedia langlink %s→%s for %r failed: %s",
                source_lang,
                target_lang,
                title,
                exc,
            )
        return None


_default_resolver: WikipediaResolver | None = None


def get_wikipedia_resolver() -> WikipediaResolver:
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = WikipediaResolver()
    return _default_resolver


def reset_wikipedia_resolver() -> None:
    """Clear the process-wide resolver cache (tests)."""
    global _default_resolver
    _default_resolver = None


def wiki_lang_for_target(target_lang: str) -> str | None:
    return _TARGET_TO_WIKI_LANG.get(target_lang.strip().lower())


def parse_wikipedia_href(href: str) -> tuple[str, str, str] | None:
    """Return ``(wiki_lang, article_title, fragment)`` or ``None``."""
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower()
    if not host.endswith("wikipedia.org"):
        return None
    lang = host.split(".")[0]
    if lang in {"www", "m"} or len(lang) < 2:
        return None
    if not parsed.path.startswith(_WIKI_PATH):
        return None
    raw_slug = unquote(parsed.path[len(_WIKI_PATH) :])
    if not raw_slug:
        return None
    slug = raw_slug.split("#", 1)[0]
    title = slug.replace("_", " ")
    fragment = ""
    if "#" in raw_slug:
        fragment = "#" + raw_slug.split("#", 1)[1]
    elif parsed.fragment:
        fragment = "#" + parsed.fragment
    return lang, title, fragment


def format_wikipedia_href(wiki_lang: str, title: str, fragment: str = "") -> str:
    slug = title.replace(" ", "_")
    path = f"{_WIKI_PATH}{slug}"
    href = urlunparse(("https", f"{wiki_lang}.wikipedia.org", path, "", "", ""))
    return href + fragment


def resolve_wikipedia_href(
    href: str,
    *,
    target_lang: str,
    resolver: WikipediaResolver | None = None,
) -> str | None:
    """Map a Wikipedia URL to the equivalent article in ``target_lang``."""
    parsed = parse_wikipedia_href(href)
    if parsed is None:
        return None
    wiki_target = wiki_lang_for_target(target_lang)
    if wiki_target is None:
        return None

    source_lang, title, fragment = parsed
    lookup_lang = source_lang
    if source_lang == "en" and _CYRILLIC.search(title):
        lookup_lang = "ru"

    if lookup_lang == wiki_target and not (
        wiki_target == "en" and _CYRILLIC.search(title)
    ):
        return href

    resolver = resolver or get_wikipedia_resolver()
    resolved = resolver.resolve_title(lookup_lang, title, wiki_target)
    if resolved is None:
        if lookup_lang != wiki_target:
            return href.replace(
                f"{lookup_lang}.wikipedia.org",
                f"{wiki_target}.wikipedia.org",
            )
        return href
    return format_wikipedia_href(wiki_target, resolved, fragment)
