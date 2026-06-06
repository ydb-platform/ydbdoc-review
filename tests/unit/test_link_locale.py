"""Tests for deterministic link URL locale fixes."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.reinsert import reinsert_segments
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.validation.link_locale import (
    check_link_locale_in_en,
    localize_links_in_document,
    mirror_link_href,
)


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("https://ru.wikipedia.org/wiki/Foo", "https://en.wikipedia.org/wiki/Foo"),
        ("https://ydb.tech/docs/ru/concepts/topic", "https://ydb.tech/docs/en/concepts/topic"),
        ("https://yandex.cloud/ru/docs/foo", "https://yandex.cloud/en/docs/foo"),
        ("https://kubernetes.io/ru/docs/home/", "https://kubernetes.io/docs/home/"),
        ("https://example.com/json-ru.html", "https://example.com/index.html"),
        ("#anchor", "#anchor"),
        ("", ""),
    ],
)
def test_mirror_link_href(href: str, expected: str) -> None:
    assert mirror_link_href(href) == expected


def test_check_link_locale_flags_en_wikipedia_with_russian_slug():
    md = (
        "See [CoW](https://en.wikipedia.org/wiki/%D0%9A%D0%BE%D0%BF%D0%B8%D1%80%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D0%B5_%D0%BF%D1%80%D0%B8_%D0%B7%D0%B0%D0%BF%D0%B8%D1%81%D0%B8).\n"
    )
    issues = check_link_locale_in_en(md)
    assert len(issues) == 1
    assert "en.wikipedia.org uses Russian article slug" in issues[0]


def test_check_link_locale_accepts_english_wikipedia_slug():
    md = "See [CoW](https://en.wikipedia.org/wiki/Copy-on-write).\n"
    assert check_link_locale_in_en(md) == []


def test_check_link_locale_flags_ru_host_in_en_document():
    md = "See [docs](https://ydb.tech/docs/ru/concepts/topic).\n"
    issues = check_link_locale_in_en(md)
    assert len(issues) == 1
    assert "RU-locale URL in EN document" in issues[0]


def test_check_link_locale_skipped_for_ru_target():
    md = "См. [wiki](https://ru.wikipedia.org/wiki/Foo).\n"
    assert check_link_locale_in_en(md, target_lang="ru") == []


def test_localize_links_in_document_table_cell():
    md = (
        "| RU |\n"
        "| --- |\n"
        "| [wiki](https://ru.wikipedia.org/wiki/X) |\n"
    )
    doc = parse_markdown(md)
    localize_links_in_document(doc)
    out = render_markdown(doc)
    assert "en.wikipedia.org" in out
    assert "ru.wikipedia.org" not in out
