"""Tests for merging PR file change lists."""

from __future__ import annotations

from ydbdoc_review.github.pr import merge_pr_file_changes


def test_merge_pr_file_changes_unions_paths():
    git = [("ydb/docs/ru/a.md", "modified")]
    api = [
        ("ydb/docs/ru/a.md", "modified"),
        ("ydb/docs/ru/b.md", "added"),
    ]
    merged = merge_pr_file_changes(git, api)
    assert merged == [
        ("ydb/docs/ru/a.md", "modified"),
        ("ydb/docs/ru/b.md", "added"),
    ]


def test_merge_pr_file_changes_prefers_stronger_kind():
    git = [("ydb/docs/ru/a.md", "modified")]
    api = [("ydb/docs/ru/a.md", "added")]
    merged = merge_pr_file_changes(git, api)
    assert merged == [("ydb/docs/ru/a.md", "added")]
