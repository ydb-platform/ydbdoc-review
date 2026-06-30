"""Tests for translation PR verify scope filtering."""

from __future__ import annotations

from ydbdoc_review.pipeline.pairs import (
    DocPair,
    NavigationPair,
    filter_translation_pr_verify_scope,
)


def test_filter_translation_pr_verify_scope_keeps_en_diff_only():
    pairs = [
        DocPair(
            ru_path="ydb/docs/ru/a.md",
            en_path="ydb/docs/en/a.md",
            ru_changed=True,
        ),
        DocPair(
            ru_path="ydb/docs/ru/b.md",
            en_path="ydb/docs/en/b.md",
            ru_changed=True,
        ),
    ]
    nav_pairs = [
        NavigationPair(
            ru_path="ydb/docs/ru/x/toc_i.yaml",
            en_path="ydb/docs/en/x/toc_i.yaml",
            ru_changed=True,
            en_changed=True,
        ),
        NavigationPair(
            ru_path="ydb/docs/ru/y/toc_i.yaml",
            en_path="ydb/docs/en/y/toc_i.yaml",
            ru_changed=True,
            supplement_only=True,
        ),
    ]
    changes = [
        ("ydb/docs/en/a.md", "modified"),
        ("ydb/docs/ru/b.md", "modified"),
        ("ydb/docs/en/x/toc_i.yaml", "modified"),
        ("ydb/docs/en/core/concepts/query_execution/spilling.md", "modified"),
    ]
    scoped_pairs, scoped_nav = filter_translation_pr_verify_scope(pairs, nav_pairs, changes)
    assert [p.en_path for p in scoped_pairs] == ["ydb/docs/en/a.md"]
    assert [n.en_path for n in scoped_nav] == ["ydb/docs/en/x/toc_i.yaml"]
