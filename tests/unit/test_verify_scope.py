"""Tests for translation PR verify scope filtering."""

from __future__ import annotations

from ydbdoc_review.pipeline.completeness import (
    href_only_source_noop_satisfied,
    translation_pr_scope_gaps,
)
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


def test_translation_pr_scope_gaps_block_false_green_for_missing_source_files():
    """Regression for #40385 -> #50838: critic saw one file and missed two."""
    expected_pairs = [
        DocPair(
            ru_path="ydb/docs/ru/core/reference/configuration/monitoring_config.md",
            en_path="ydb/docs/en/core/reference/configuration/monitoring_config.md",
            ru_changed=True,
        ),
        DocPair(
            ru_path="ydb/docs/ru/core/reference/configuration/tls.md",
            en_path="ydb/docs/en/core/reference/configuration/tls.md",
            ru_changed=True,
        ),
    ]
    expected_nav = [
        NavigationPair(
            ru_path="ydb/docs/ru/core/security/toc_p.yaml",
            en_path="ydb/docs/en/core/security/toc_p.yaml",
            ru_changed=True,
        ),
        NavigationPair(
            ru_path="ydb/docs/ru/core/toc_p.yaml",
            en_path="ydb/docs/en/core/toc_p.yaml",
            ru_changed=True,
            supplement_only=True,
        ),
    ]
    translation_changes = [
        ("ydb/docs/en/core/reference/configuration/tls.md", "modified"),
    ]

    assert translation_pr_scope_gaps(expected_pairs, expected_nav, translation_changes) == [
        "ydb/docs/en/core/reference/configuration/monitoring_config.md",
        "ydb/docs/en/core/security/toc_p.yaml",
    ]


def test_translation_pr_scope_gaps_ignore_bilingual_source_navigation():
    expected_nav = [
        NavigationPair(
            ru_path="ydb/docs/ru/core/toc_p.yaml",
            en_path="ydb/docs/en/core/toc_p.yaml",
            ru_changed=True,
        )
    ]

    assert (
        translation_pr_scope_gaps(
            [],
            expected_nav,
            [],
            already_satisfied=frozenset({"ydb/docs/en/core/toc_p.yaml"}),
        )
        == []
    )


def test_href_only_source_noop_satisfied_for_pr_50976():
    source_base = "[YDB Monitoring](../ydb-ui/ydb-monitoring.md)\n"
    source_head = "[YDB Monitoring](../embedded-ui/ydb-monitoring.md)\n"
    current_ru = "[YDB Monitoring](../ydb-ui/ydb-monitoring.md)\n"
    current_en = "[YDB Monitoring](../ydb-ui/ydb-monitoring.md)\n"

    assert href_only_source_noop_satisfied(
        source_base, source_head, current_ru, current_en
    )
    assert not href_only_source_noop_satisfied(
        source_base, source_head, current_ru, "[YDB Monitoring](broken.md)\n"
    )
