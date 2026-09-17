"""F-060: localize external links only when the pair is confirmed."""

from unittest.mock import MagicMock

from ydbdoc_review.validation import wikipedia_links
from ydbdoc_review.validation.link_locale import mirror_link_href
from ydbdoc_review.validation.wikipedia_links import (
    WikipediaResolver,
    resolve_wikipedia_href,
)


def test_F060_preserve_external():
    for href in (
        "https://api.example.test/v1/items?lang=ru#item",
        "https://example.test/docs/reference.md#api",
        "mailto:docs@example.test",
    ):
        assert mirror_link_href(href) == href


def test_F060_wikipedia_pair(caplog, monkeypatch):
    resolver = MagicMock(spec=WikipediaResolver)
    resolver.resolve_title.side_effect = [
        "Copy-on-write",
        None,
        None,
    ]
    monkeypatch.setattr(wikipedia_links, "get_wikipedia_resolver", lambda: resolver)

    confirmed = resolve_wikipedia_href(
        "https://ru.wikipedia.org/wiki/Копирование_при_записи#history",
        target_lang="en",
        resolver=resolver,
    )
    assert confirmed == "https://en.wikipedia.org/wiki/Copy-on-write#history"

    unresolved_href = "https://ru.wikipedia.org/wiki/Unknown_page#keep-me"
    with caplog.at_level("WARNING"):
        unresolved = resolve_wikipedia_href(
            unresolved_href,
            target_lang="en",
            resolver=resolver,
        )
    assert unresolved is None
    assert any("resolve failed" in record.message for record in caplog.records)
    assert mirror_link_href(unresolved_href) == unresolved_href
