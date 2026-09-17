"""Tests for RU/EN path pairing."""

from __future__ import annotations

from ydbdoc_review.pipeline.pairs import (
    build_doc_pairs,
    counterpart,
    is_docs_markdown,
    is_language_neutral_docs_path,
    locale_of,
)


def test_counterpart_ru_en():
    root = "ydb/docs"
    assert counterpart("ydb/docs/ru/foo/bar.md", root) == "ydb/docs/en/foo/bar.md"
    assert counterpart("ydb/docs/en/foo/bar.md", root) == "ydb/docs/ru/foo/bar.md"


def test_is_docs_markdown_excludes_root_neutral_includes():
    assert is_docs_markdown("ydb/docs/ru/x.md", "ydb/docs")
    assert not is_docs_markdown("ydb/docs/_includes/x.md", "ydb/docs")
    assert is_language_neutral_docs_path("ydb/docs/_includes/x.md", "ydb/docs")


def test_is_docs_markdown_includes_locale_mirror_includes():
    path = "ydb/docs/ru/core/integrations/orm/_includes/toc-table.md"
    assert is_docs_markdown(path, "ydb/docs")
    assert counterpart(path, "ydb/docs") == (
        "ydb/docs/en/core/integrations/orm/_includes/toc-table.md"
    )


def test_build_doc_pairs_locale_include():
    ru = "ydb/docs/ru/core/integrations/orm/_includes/toc-table.md"
    changes = [(ru, "modified")]
    pairs = build_doc_pairs(changes)
    assert len(pairs) == 1
    assert pairs[0].ru_path == ru
    assert pairs[0].en_path.endswith("/en/core/integrations/orm/_includes/toc-table.md")


def test_build_doc_pairs_merged_flags():
    changes = [
        ("ydb/docs/ru/guide.md", "modified"),
        ("ydb/docs/en/guide.md", "modified"),
    ]
    pairs = build_doc_pairs(changes)
    assert len(pairs) == 1
    assert pairs[0].ru_changed
    assert pairs[0].en_changed


def test_build_doc_pairs_ru_deleted():
    pairs = build_doc_pairs([("ydb/docs/ru/old.md", "deleted")])
    assert len(pairs) == 1
    assert pairs[0].ru_deleted
    assert pairs[0].ru_changed


def test_locale_of():
    assert locale_of("ydb/docs/ru/a.md", "ydb/docs") == "ru"
    assert locale_of("ydb/docs/en/a.md", "ydb/docs") == "en"
