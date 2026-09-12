"""F-032 contract: locale-neutral resources stay outside translation."""

from __future__ import annotations

from ydbdoc_review.parsing.include_paths import resolve_locale_md_path
from ydbdoc_review.pipeline.pairs import build_doc_pairs


def test_F032_outside_roots() -> None:
    """Changes outside the RU/EN roots do not become translation pairs."""
    changes = [
        ("ydb/docs/_includes/shared.md", "modified"),
        ("ydb/docs/assets/logo.svg", "modified"),
        ("ydb/docs/ru/guide.md", "modified"),
    ]

    pairs = build_doc_pairs(changes, docs_root="ydb/docs")

    assert [(pair.ru_path, pair.en_path) for pair in pairs] == [
        ("ydb/docs/ru/guide.md", "ydb/docs/en/guide.md"),
    ]


def test_F032_neutral_include() -> None:
    """A shared include can be read, but has no localized copy to write."""
    shared_path = "ydb/docs/_includes/shared.md"
    files = {shared_path: "Shared content."}

    resolved = resolve_locale_md_path(
        "ydb/docs/ru/guide.md",
        "/ydb/docs/_includes/shared.md",
        docs_root="ydb/docs",
    )

    assert files[shared_path] == "Shared content."
    assert resolved is None
