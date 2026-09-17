"""F-066: migrate structural fragment usages without translating prose."""

from __future__ import annotations

from ydbdoc_review.validation.fragment_repair import rewrite_structural_fragment_usages


def test_F066_all_usages() -> None:
    usages = (
        "See [section](page.md#old-id). Keep this prose: old-id.\n",
        "- name: Section\n  href: page.md#old-id\n",
        "{% include [Section](page.md#old-id) %}\n",
    )

    for text in usages:
        migrated = rewrite_structural_fragment_usages(
            text,
            old_fragment="old-id",
            new_fragment="canonical-id",
        )
        assert "#canonical-id" in migrated
        assert "#old-id" not in migrated
        assert "old-id." in migrated or "old-id" not in migrated


def test_F066_source_permission() -> None:
    source = "## Раздел {#old-id}\n\nRU prose old-id stays untouched.\n"

    assert rewrite_structural_fragment_usages(
        source,
        old_fragment="old-id",
        new_fragment="canonical-id",
    ) == source
    assert rewrite_structural_fragment_usages(
        source,
        old_fragment="old-id",
        new_fragment="canonical-id",
        allow_source=True,
    ) == source
