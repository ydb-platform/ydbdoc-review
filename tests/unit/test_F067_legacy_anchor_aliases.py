"""F-067: legacy fragments remain aliases of one existing heading."""

from __future__ import annotations

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.validation.fragment_repair import fragment_declared_in_markdown
from ydbdoc_review.validation.yfm_anchor import (
    JobAnchorDictionary,
    _iter_headings,
    _legacy_transliterated_slug,
    english_yfm_anchor,
)


def test_F067_legacy_urls() -> None:
    markdown = "## Параметры TLS-соединения\n"
    headings = list(_iter_headings(parse_markdown(markdown).children))
    legacy = _legacy_transliterated_slug("Параметры TLS-соединения")

    assert len(headings) == 1
    assert fragment_declared_in_markdown(markdown, "параметры-tls-соединения")
    assert fragment_declared_in_markdown(markdown, legacy)
    assert not fragment_declared_in_markdown(markdown, "unrelated-id")


def test_F067_ttl_new_links() -> None:
    dictionary = JobAnchorDictionary()
    canonical = dictionary.lookup_or_insert("поля", "Field descriptions")

    # Reusing the same job dictionary after an arbitrary context boundary keeps
    # the established alias mapping; new generated links are canonical ASCII.
    assert dictionary.lookup_or_insert("поля", "Different title") == canonical
    assert english_yfm_anchor("поля", "Field descriptions").isascii()
    assert canonical == "field-descriptions"
