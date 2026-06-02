"""Tests for deterministic link URL locale fixes."""

from __future__ import annotations

import pytest

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.reinsert import reinsert_segments
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.validation.link_locale import localize_links_in_document, mirror_link_href


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
