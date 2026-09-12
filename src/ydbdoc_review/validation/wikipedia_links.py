"""Resolve Wikipedia article titles via the MediaWiki API (no LLM).

Resolution chain for cross-locale hrefs (§6.130):

1. MediaWiki ``langlinks`` (timeout ``timeout_s``)
2. Wikidata sitelink (same timeout)
3. Offline ``_OFFLINE_EN_TITLES`` / fragment map
4. ``None`` — caller decides (do **not** return the original RU href)
"""

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

# Default MediaWiki/Wikidata timeout (AGENT_TASKS: 5s).
_DEFAULT_TIMEOUT_S = 5.0


@dataclass
class WikipediaResolver:
    """Cached langlink lookups between Wikipedia language editions."""

    timeout_s: float = _DEFAULT_TIMEOUT_S
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
                logger.warning(
                    "Wikipedia langlink miss %s→%s for %r; trying Wikidata",
                    source_lang,
                    target_lang,
                    title,
                )
                resolved = self._fetch_wikidata_sitelink(
                    source_lang, title, target_lang
                )
            if resolved is None:
                logger.warning(
                    "Wikipedia Wikidata miss %s→%s for %r; will try offline map",
                    source_lang,
                    target_lang,
                    title,
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


def _is_valid_wikipedia_url(href: str, wiki_lang: str, title: str) -> bool:
    """Validate parsed Wikipedia URL pieces (format, non-empty title)."""
    if not href or not isinstance(href, str):
        return False
    if not wiki_lang or len(wiki_lang) < 2 or not wiki_lang.isalpha():
        return False
    if not title or not title.strip():
        return False
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower()
    if not host.endswith("wikipedia.org"):
        return False
    if not parsed.path.startswith(_WIKI_PATH):
        return False
    # Reject NUL / control characters in title
    if any(ord(ch) < 32 for ch in title):
        return False
    return True


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
    if not _is_valid_wikipedia_url(href, lang, title):
        return None
    return lang, title, fragment


def format_wikipedia_href(wiki_lang: str, title: str, fragment: str = "") -> str:
    slug = title.replace(" ", "_")
    path = f"{_WIKI_PATH}{slug}"
    href = urlunparse(("https", f"{wiki_lang}.wikipedia.org", path, "", "", ""))
    return href + fragment


# Offline fallbacks when MediaWiki/Wikidata are unreachable (CI TLS flakiness).
# Keys: (source_wiki_lang, title) → English article title. Hand-curated from
# ydb/docs RU Wikipedia usage (§6.129 / §6.130) — do not auto-generate from JSON.
_OFFLINE_EN_TITLES: dict[tuple[str, str], str] = {
    # Previously shipped
    ("ru", "Язык определения данных"): "Data definition language",
    ("ru", "Язык манипулирования данными"): "Data manipulation language",
    ("ru", "Data Definition Language"): "Data definition language",
    ("ru", "Data Manipulation Language"): "Data manipulation language",
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
    # Same-title / Latin tech tokens (common in RU docs)
    ("ru", "JSON"): "JSON",
    ("ru", "ISO 8601"): "ISO 8601",
    ("ru", "Base64"): "Base64",
    ("ru", "CSV"): "Comma-separated values",
    ("ru", "TSV"): "Tab-separated values",
    ("ru", "UTF-8"): "UTF-8",
    ("ru", "UUID"): "UUID",
    ("ru", "ACID"): "ACID",
    ("ru", "LDAP"): "Lightweight Directory Access Protocol",
    ("ru", "OLTP"): "Online transaction processing",
    ("ru", "OLAP"): "Online analytical processing",
    ("ru", "FQDN"): "Fully qualified domain name",
    ("ru", "SQL:2016"): "SQL:2016",
    ("ru", "PostgreSQL"): "PostgreSQL",
    ("ru", "Apache Kafka"): "Apache Kafka",
    ("ru", "Protocol Buffers"): "Protocol Buffers",
    ("ru", "Network File System"): "Network File System",
    ("ru", "Resident set size"): "Resident set size",
    ("ru", "Transport Layer Security"): "Transport Layer Security",
    ("ru", "Common Language Runtime"): "Common Language Runtime",
    ("ru", "Java Database Connectivity"): "Java Database Connectivity",
    # Compression / codecs
    ("ru", "Zstandard"): "Zstandard",
    ("ru", "Gzip"): "Gzip",
    ("ru", "LZ4"): "LZ4",
    ("ru", "Brotli"): "Brotli",
    ("ru", "Bzip2"): "bzip2",
    ("ru", "XZ"): "XZ Utils",
    ("ru", "Lzop"): "LZO",
    ("ru", "Snappy (библиотека)"): "Snappy (compression)",
    # Algorithms / CS
    ("ru", "Unix-время"): "Unix time",
    ("ru", "Регулярные выражения"): "Regular expression",
    ("ru", "Юникод"): "Unicode",
    ("ru", "Порядок байтов"): "Endianness",
    ("ru", "Копирование при записи"): "Copy-on-write",
    ("ru", "Свёртка констант"): "Constant folding",
    ("ru", "Абстрактное синтаксическое дерево"): "Abstract syntax tree",
    ("ru", "Цепь Маркова"): "Markov chain",
    ("ru", "Round-robin (алгоритм)"): "Round-robin scheduling",
    ("ru", "Средняя загрузка"): "Load (computing)",
    ("ru", "Среднеквадратическое отклонение"): "Standard deviation",
    ("ru", "Полнота и точность"): "Precision and recall",
    ("ru", "Внедрение SQL-кода"): "SQL injection",
    ("ru", "Argon2"): "Argon2",
    # Systems / ops
    ("ru", "Аутентификация"): "Authentication",
    ("ru", "Авторизация"): "Authorization",
    ("ru", "Нагрузочное тестирование"): "Load testing",
    ("ru", "Путь к файлу"): "Path (computing)",
    ("ru", "Цепочка доверия"): "Chain of trust",
    ("ru", "Система управления версиями"): "Version control",
    ("ru", "Инфраструктура как код"): "Infrastructure as code",
    ("ru", "Непрерывная интеграция"): "Continuous integration",
    ("ru", "Непрерывная доставка"): "Continuous delivery",
    ("ru", "Дамп памяти"): "Core dump",
    ("ru", "Контрольная группа (Linux)"): "Cgroups",
    ("ru", "Издатель — подписчик"): "Publish–subscribe pattern",
    ("ru", "Очередь сообщений"): "Message queue",
    ("ru", "Семафор (программирование)"): "Semaphore (programming)",
    ("ru", "Мьютекс"): "Mutual exclusion",
    ("ru", "Таблица (база данных)"): "Table (database)",
    ("ru", "Индекс (базы данных)"): "Database index",
    ("ru", "Большая языковая модель"): "Large language model",
    ("ru", "Медленно меняющееся измерение"): "Slowly changing dimension",
    ("ru", "Заголовочный регистр"): "Letter case",
    ("ru", "RAID"): "RAID",
}

# When the RU fragment is Cyrillic / locale-specific, map to EN section id.
# Key: (english_article_title, ru_fragment_without_hash) → en_fragment_without_hash
_OFFLINE_EN_FRAGMENTS: dict[tuple[str, str], str] = {
    (
        "Isolation (database systems)",
        "Serializable_(упорядочиваемость)",
    ): "Serializable",
}


def _lookup_lang_for_title(source_lang: str, title: str) -> str:
    """Prefer RU lookup when an EN host carries a Cyrillic article slug."""
    if source_lang == "en" and _CYRILLIC.search(title):
        return "ru"
    return source_lang


def _map_fragment(resolved_title: str, fragment: str) -> str:
    """Map or drop locale-specific ``#fragment`` after title resolution."""
    if not fragment.startswith("#"):
        return fragment
    frag_key = unquote(fragment[1:])
    mapped = _OFFLINE_EN_FRAGMENTS.get((resolved_title, frag_key))
    if mapped is None:
        # Also try underscore form used in raw URLs
        mapped = _OFFLINE_EN_FRAGMENTS.get(
            (resolved_title, frag_key.replace(" ", "_"))
        )
    if mapped is not None:
        return f"#{mapped}"
    if _CYRILLIC.search(frag_key):
        logger.warning(
            "Dropping unmapped Cyrillic Wikipedia fragment %r on EN article %r",
            frag_key,
            resolved_title,
        )
        return ""
    return fragment


def resolve_wikipedia_href(
    href: str,
    *,
    target_lang: str,
    resolver: WikipediaResolver | None = None,
) -> str | None:
    """Map a Wikipedia URL to the equivalent article in ``target_lang``.

    Returns ``None`` when the URL is not Wikipedia, the target lang is unknown,
    or no EN title could be resolved (API + offline). Callers must not treat
    ``None`` as «keep the RU href» for EN output — leave the link for
    heuristics / manual fix instead of inventing a broken EN slug.
    """
    parsed = parse_wikipedia_href(href)
    if parsed is None:
        return None
    wiki_target = wiki_lang_for_target(target_lang)
    if wiki_target is None:
        return None

    source_lang, title, fragment = parsed
    if not _is_valid_wikipedia_url(href, source_lang, title):
        return None

    lookup_lang = _lookup_lang_for_title(source_lang, title)

    # Already on the target edition with a non-Cyrillic slug — keep as-is.
    if lookup_lang == wiki_target and not (
        wiki_target == "en" and _CYRILLIC.search(title)
    ):
        return href

    resolver = resolver or get_wikipedia_resolver()
    resolved = resolver.resolve_title(lookup_lang, title, wiki_target)
    if resolved is None and wiki_target == "en":
        resolved = _OFFLINE_EN_TITLES.get((lookup_lang, title))
        if resolved is not None:
            logger.warning(
                "Wikipedia offline map hit %s→en for %r → %r",
                lookup_lang,
                title,
                resolved,
            )
    if resolved is None:
        logger.warning(
            "Wikipedia resolve failed %s→%s for %r (href=%s); returning None",
            lookup_lang,
            wiki_target,
            title,
            href,
        )
        return None

    out_fragment = _map_fragment(resolved, fragment)
    return format_wikipedia_href(wiki_target, resolved, out_fragment)
