"""Tests for navigation pair detection."""

from __future__ import annotations

from ydbdoc_review.pipeline.pairs import build_navigation_pairs


def test_build_navigation_pairs_from_toc_yaml():
    changes = [
        (
            "ydb/docs/ru/core/yql/reference/syntax/alter_table/toc_i.yaml",
            "modified",
        ),
        (
            "ydb/docs/ru/core/yql/reference/syntax/alter_table/compact.md",
            "added",
        ),
    ]
    pairs = build_navigation_pairs(changes)
    assert len(pairs) == 1
    assert pairs[0].ru_path.endswith("/ru/core/yql/reference/syntax/alter_table/toc_i.yaml")
    assert pairs[0].en_path.endswith("/en/core/yql/reference/syntax/alter_table/toc_i.yaml")
    assert pairs[0].ru_changed is True
