"""Tests for Wikipedia langlink resolution."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ydbdoc_review.validation.wikipedia_links import (
    WikipediaResolver,
    format_wikipedia_href,
    parse_wikipedia_href,
    resolve_wikipedia_href,
)


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


@pytest.mark.integration
def test_resolver_live_ru_to_en_copy_on_write():
    resolver = WikipediaResolver(timeout_s=15.0)
    title = resolver.resolve_title("ru", "Копирование при записи", "en")
    assert title == "Copy-on-write"
