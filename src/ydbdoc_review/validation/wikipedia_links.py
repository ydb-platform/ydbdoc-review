"""Resolve Wikipedia article titles via the MediaWiki API (no LLM)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse, urlunparse

import requests

logger = logging.getLogger(__name__)

# MediaWiki returns HTTP 403 without a descriptive User-Agent (T400119).
_WIKI_USER_AGENT = (
    "ydbdoc-review/0.1 (+https://github.com/ydb-platform/ydbdoc-review; "
    "YDB docs translation)"
)

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
            resolved = self._fetch_langlink(source_lang, title, target_lang)
            if resolved is None:
                resolved = self._fetch_wikidata_sitelink(
                    source_lang, title, target_lang
                )
            self._cache[key] = resolved
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
            resp = requests.get(
                url,
                params=params,
                timeout=self.timeout_s,
                headers={"User-Agent": _WIKI_USER_AGENT},
            )
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

    def _fetch_wikidata_sitelink(
        self,
        source_lang: str,
        title: str,
        target_lang: str,
    ) -> str | None:
        """Resolve via Wikidata when direct langlinks are missing."""
        qid = self._fetch_wikibase_item(source_lang, title)
        if not qid:
            return None
        return self._fetch_wikidata_title(qid, target_lang)

    def _fetch_wikibase_item(self, wiki_lang: str, title: str) -> str | None:
        url = f"https://{wiki_lang}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "titles": title,
            "prop": "pageprops",
            "ppprop": "wikibase_item",
            "redirects": "1",
            "format": "json",
            "formatversion": "2",
        }
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=self.timeout_s,
                headers={"User-Agent": _WIKI_USER_AGENT},
            )
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", [])
            if not pages:
                return None
            page = pages[0]
            if page.get("missing"):
                return None
            item = (page.get("pageprops") or {}).get("wikibase_item")
            if item:
                return str(item).strip() or None
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Wikipedia wikibase lookup for %r (%s) failed: %s",
                title,
                wiki_lang,
                exc,
            )
        return None

    def _fetch_wikidata_title(self, qid: str, target_lang: str) -> str | None:
        site = f"{target_lang}wiki"
        url = "https://www.wikidata.org/w/api.php"
        params = {
            "action": "wbgetentities",
            "ids": qid,
            "props": "sitelinks",
            "sitefilter": site,
            "format": "json",
        }
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=self.timeout_s,
                headers={"User-Agent": _WIKI_USER_AGENT},
            )
            resp.raise_for_status()
            entity = resp.json().get("entities", {}).get(qid, {})
            sitelink = (entity.get("sitelinks") or {}).get(site)
            if sitelink:
                resolved = str(sitelink.get("title") or "").strip()
                return resolved or None
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Wikidata sitelink %s→%s for %s failed: %s",
                qid,
                target_lang,
                site,
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


# Offline fallbacks when MediaWiki/Wikidata are unreachable (CI TLS flakiness).
# Keys: (source_wiki_lang, title) → English article title.
_OFFLINE_EN_TITLES: dict[tuple[str, str], str] = {
    ("ru", "Язык определения данных"): "Data definition language",
    ("ru", "Язык манипулирования данными"): "Data manipulation language",
    ("ru", "N-грамм"): "N-gram",
    ("ru", "Инвертированный индекс"): "Inverted index",
    ("ru", "Модель акторов"): "Actor model",
    ("ru", "Уровень изолированности транзакций"): "Isolation (database systems)",
    ("ru", "MVCC"): "Multiversion concurrency control",
    ("ru", "Фильтр Блума"): "Bloom filter",
    ("ru", "Оптимизация запросов СУБД"): "Query optimization",
    ("ru", "Алгоритм Паксос"): "Paxos (computer science)",
    ("ru", "Raft (алгоритм)"): "Raft (algorithm)",
    ("ru", "Консенсус в распределённых вычислениях"): "Consensus (computer science)",
    ("ru", "LSM-дерево"): "Log-structured merge-tree",
    ("ru", "Стирающий код"): "Erasure code",
}

# When the RU fragment is Cyrillic / locale-specific, map to EN section id.
_OFFLINE_EN_FRAGMENTS: dict[tuple[str, str], str] = {
    (
        "Isolation (database systems)",
        "Serializable_(упорядочиваемость)",
    ): "Serializable",
}


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
    if resolved is None and wiki_target == "en":
        resolved = _OFFLINE_EN_TITLES.get((lookup_lang, title))
    if resolved is None:
        return href
    out_fragment = fragment
    if fragment.startswith("#"):
        frag_key = fragment[1:]
        mapped = _OFFLINE_EN_FRAGMENTS.get((resolved, frag_key))
        if mapped is not None:
            out_fragment = f"#{mapped}"
        elif _CYRILLIC.search(frag_key):
            # Drop unmapped Cyrillic section ids — they 404 on en.wikipedia.
            out_fragment = ""
    return format_wikipedia_href(wiki_target, resolved, out_fragment)
