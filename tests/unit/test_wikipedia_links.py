"""Tests for Wikipedia langlink resolution (§6.130)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from ydbdoc_review.validation.wikipedia_links import (
    WikipediaResolver,
    _OFFLINE_EN_TITLES,
    _is_valid_wikipedia_url,
    format_wikipedia_href,
    parse_wikipedia_href,
    resolve_wikipedia_href,
)


def test_offline_map_has_at_least_fifty_entries():
    assert len(_OFFLINE_EN_TITLES) >= 50


def test_parse_wikipedia_href_decodes_slug():
    href = (
        "https://en.wikipedia.org/wiki/"
        "%D0%9A%D0%BE%D0%BF%D0%B8%D1%80%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D0%B5_%D0%BF%D1%80%D0%B8_%D0%B7%D0%B0%D0%BF%D0%B8%D1%81%D0%B8"
    )
    parsed = parse_wikipedia_href(href)
    assert parsed is not None
    lang, title, fragment = parsed
    assert lang == "en"
    assert title == "Копирование при записи"
    assert fragment == ""


def test_is_valid_wikipedia_url_rejects_empty_title():
    assert not _is_valid_wikipedia_url(
        "https://ru.wikipedia.org/wiki/", "ru", ""
    )


def test_format_wikipedia_href_uses_underscores():
    assert (
        format_wikipedia_href("en", "Copy-on-write")
        == "https://en.wikipedia.org/wiki/Copy-on-write"
    )


def test_resolve_wikipedia_href_ru_to_en():
    resolver = MagicMock(spec=WikipediaResolver)
    resolver.resolve_title.return_value = "Copy-on-write"
    href = "https://ru.wikipedia.org/wiki/Копирование_при_записи"
    out = resolve_wikipedia_href(href, target_lang="en", resolver=resolver)
    assert out == "https://en.wikipedia.org/wiki/Copy-on-write"
    resolver.resolve_title.assert_called_once_with(
        "ru", "Копирование при записи", "en"
    )


def test_resolve_wikipedia_href_en_cyrillic_slug_uses_ru_lookup():
    resolver = MagicMock(spec=WikipediaResolver)
    resolver.resolve_title.return_value = "Copy-on-write"
    href = (
        "https://en.wikipedia.org/wiki/"
        "%D0%9A%D0%BE%D0%BF%D0%B8%D1%80%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D0%B5_%D0%BF%D1%80%D0%B8_%D0%B7%D0%B0%D0%BF%D0%B8%D1%81%D0%B8"
    )
    out = resolve_wikipedia_href(href, target_lang="en", resolver=resolver)
    assert out == "https://en.wikipedia.org/wiki/Copy-on-write"
    resolver.resolve_title.assert_called_once_with(
        "ru", "Копирование при записи", "en"
    )


def test_fetch_langlink_sends_user_agent(monkeypatch):
    resolver = WikipediaResolver(timeout_s=5.0)
    captured: dict[str, object] = {}

    def fake_get(url, **kwargs):
        captured["headers"] = kwargs.get("headers")
        captured["timeout"] = kwargs.get("timeout")
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "query": {
                "pages": [
                    {
                        "langlinks": [{"lang": "en", "title": "Copy-on-write"}],
                    }
                ]
            }
        }
        return response

    monkeypatch.setattr(requests, "get", fake_get)
    assert resolver.resolve_title("ru", "Копирование при записи", "en") == (
        "Copy-on-write"
    )
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "ydbdoc-review" in str(headers["User-Agent"])
    assert captured["timeout"] == 5.0


def test_resolve_wikipedia_href_unresolved_returns_none():
    """§6.130: full miss → None (not the original RU href)."""
    resolver = MagicMock(spec=WikipediaResolver)
    resolver.resolve_title.return_value = None
    href = "https://ru.wikipedia.org/wiki/Несуществующая_статья_xyz"
    # Ensure offline map has no hit
    assert ("ru", "Несуществующая статья xyz") not in _OFFLINE_EN_TITLES
    out = resolve_wikipedia_href(href, target_lang="en", resolver=resolver)
    assert out is None


def test_resolve_wikipedia_href_offline_ddl_map_when_lookup_fails():
    resolver = MagicMock(spec=WikipediaResolver)
    resolver.resolve_title.return_value = None
    href = (
        "https://ru.wikipedia.org/wiki/"
        "%D0%AF%D0%B7%D1%8B%D0%BA_%D0%BE%D0%BF%D1%80%D0%B5%D0%B4%D0%B5%D0%BB%D0%B5%D0%BD%D0%B8%D1%8F_%D0%B4%D0%B0%D0%BD%D0%BD%D1%8B%D1%85"
    )
    out = resolve_wikipedia_href(href, target_lang="en", resolver=resolver)
    assert out == "https://en.wikipedia.org/wiki/Data_definition_language"


def test_resolve_wikipedia_href_offline_inverted_index():
    resolver = MagicMock(spec=WikipediaResolver)
    resolver.resolve_title.return_value = None
    href = (
        "https://ru.wikipedia.org/wiki/"
        "%D0%98%D0%BD%D0%B2%D0%B5%D1%80%D1%82%D0%B8%D1%80%D0%BE%D0%B2%D0%B0%D0%BD%D0%BD%D1%8B%D0%B9_%D0%B8%D0%BD%D0%B4%D0%B5%D0%BA%D1%81"
    )
    out = resolve_wikipedia_href(href, target_lang="en", resolver=resolver)
    assert out == "https://en.wikipedia.org/wiki/Inverted_index"


def test_resolve_maps_cyrillic_fragment_via_offline_table():
    resolver = MagicMock(spec=WikipediaResolver)
    resolver.resolve_title.return_value = "Isolation (database systems)"
    href = (
        "https://ru.wikipedia.org/wiki/"
        "%D0%A3%D1%80%D0%BE%D0%B2%D0%B5%D0%BD%D1%8C_%D0%B8%D0%B7%D0%BE%D0%BB%D0%B8%D1%80%D0%BE%D0%B2%D0%B0%D0%BD%D0%BD%D0%BE%D1%81%D1%82%D0%B8_"
        "%D1%82%D1%80%D0%B0%D0%BD%D0%B7%D0%B0%D0%BA%D1%86%D0%B8%D0%B9"
        "#Serializable_(%D1%83%D0%BF%D0%BE%D1%80%D1%8F%D0%B4%D0%BE%D1%87%D0%B8%D0%B2%D0%B0%D0%B5%D0%BC%D0%BE%D1%81%D1%82%D1%8C)"
    )
    out = resolve_wikipedia_href(href, target_lang="en", resolver=resolver)
    assert out == (
        "https://en.wikipedia.org/wiki/Isolation_(database_systems)#Serializable"
    )


def test_resolve_drops_unmapped_cyrillic_fragment_with_warning(caplog):
    resolver = MagicMock(spec=WikipediaResolver)
    resolver.resolve_title.return_value = "JSON"
    href = "https://ru.wikipedia.org/wiki/JSON#Неизвестная_секция"
    with caplog.at_level("WARNING"):
        out = resolve_wikipedia_href(href, target_lang="en", resolver=resolver)
    assert out == "https://en.wikipedia.org/wiki/JSON"
    assert any("Dropping unmapped Cyrillic" in r.message for r in caplog.records)


def test_resolve_keeps_url_without_fragment():
    resolver = MagicMock(spec=WikipediaResolver)
    resolver.resolve_title.return_value = "JSON"
    out = resolve_wikipedia_href(
        "https://ru.wikipedia.org/wiki/JSON",
        target_lang="en",
        resolver=resolver,
    )
    assert out == "https://en.wikipedia.org/wiki/JSON"


def test_resolve_title_langlink_timeout_falls_back_to_wikidata(monkeypatch):
    resolver = WikipediaResolver(timeout_s=5.0)
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        response = MagicMock()
        response.raise_for_status = MagicMock()
        if "wikidata.org" in url:
            response.json.return_value = {
                "entities": {
                    "Q123": {
                        "sitelinks": {"enwiki": {"title": "Copy-on-write"}},
                    }
                }
            }
            return response
        if kwargs.get("params", {}).get("prop") == "pageprops":
            response.json.return_value = {
                "query": {
                    "pages": [{"pageprops": {"wikibase_item": "Q123"}}]
                }
            }
            return response
        # langlink: simulate timeout
        raise requests.Timeout("simulated")

    monkeypatch.setattr(requests, "get", fake_get)
    assert resolver.resolve_title("ru", "Копирование при записи", "en") == (
        "Copy-on-write"
    )
    assert any("wikipedia.org" in u for u in calls)
    assert any("wikidata.org" in u for u in calls)


def test_resolve_wikipedia_href_wikidata_fallback(monkeypatch):
    resolver = WikipediaResolver(timeout_s=5.0)

    def fake_get(url, **kwargs):
        params = kwargs.get("params") or {}
        response = MagicMock()
        response.raise_for_status = MagicMock()
        if "wikidata.org" in url:
            response.json.return_value = {
                "entities": {
                    "Q123": {
                        "sitelinks": {
                            "enwiki": {"title": "Copy-on-write"},
                        }
                    }
                }
            }
            return response
        if params.get("prop") == "langlinks":
            response.json.return_value = {"query": {"pages": [{}]}}
            return response
        if params.get("prop") == "pageprops":
            response.json.return_value = {
                "query": {
                    "pages": [{"pageprops": {"wikibase_item": "Q123"}}]
                }
            }
            return response
        response.json.return_value = {}
        return response

    monkeypatch.setattr(requests, "get", fake_get)
    href = "https://ru.wikipedia.org/wiki/Копирование_при_записи"
    out = resolve_wikipedia_href(href, target_lang="en", resolver=resolver)
    assert out == "https://en.wikipedia.org/wiki/Copy-on-write"
