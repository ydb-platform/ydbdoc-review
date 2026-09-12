"""F-071: known-address compatibility and final-tree ID integrity."""

from __future__ import annotations

from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.validation.en_link_targets import check_en_page_link_targets
from ydbdoc_review.validation.fragment_repair import fragment_declared_in_markdown

PAGE = "ydb/docs/en/core/guide/referrer.md"
TARGET = "ydb/docs/en/core/guide/target.md"


def test_F071_known_sources() -> None:
    """S, H, B and the explicit tech-writer address remain known; unknown does not."""
    authority = RuAuthority(
        source_repo="ydb-platform/ydb",
        source_pr=71,
        source_base_sha="0" * 40,
        source_head_sha="1" * 40,
        baseline_sha="2" * 40,
        ru_sha="3" * 40,
        mode="current",
    )
    assert {
        authority.source_base_sha,
        authority.source_head_sha,
        authority.baseline_sha,
        authority.ru_sha,
    } == {"0" * 40, "1" * 40, "2" * 40, "3" * 40}

    target = "## Stable section {#stable-id}\n"
    known_snapshots = [target, target, target, target]
    for snapshot in known_snapshots:
        assert fragment_declared_in_markdown(snapshot, "stable-id")
        assert check_en_page_link_targets(
            PAGE,
            "See [stable](target.md#stable-id).\n",
            read_text=lambda path, snapshot=snapshot: snapshot if path == TARGET else None,
        ) == []

    assert not fragment_declared_in_markdown(target, "never-existed")
    unknown = check_en_page_link_targets(
        PAGE,
        "See [unknown](target.md#never-existed).\n",
        read_text=lambda path: target if path == TARGET else None,
    )
    assert unknown and "missing fragment: never-existed" in unknown[0]


def test_F071_id_integrity() -> None:
    """Missing, duplicate and foreign IDs, plus changed uses, are checked in the final tree."""
    duplicate_target = "## First {#stable-id}\n\n## Second {#stable-id}\n"
    duplicate = check_en_page_link_targets(
        PAGE,
        "See [stable](target.md#stable-id).\n",
        read_text=lambda path: duplicate_target if path == TARGET else None,
    )
    assert duplicate and "duplicate explicit fragment(s): stable-id" in duplicate[0]

    foreign = check_en_page_link_targets(
        PAGE,
        "See [foreign](target.md#belongs-elsewhere).\n",
        read_text=lambda path: "## Stable section {#stable-id}\n" if path == TARGET else None,
    )
    assert foreign and "missing fragment: belongs-elsewhere" in foreign[0]

    changed_use = check_en_page_link_targets(
        PAGE,
        "See [stable](target.md#stable-id).\n",
        read_text=lambda path: "## Stable section {#stable-id}\n" if path == TARGET else None,
    )
    assert changed_use == []
