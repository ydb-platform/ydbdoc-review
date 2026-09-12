"""F-087 contracts for scoped orphan checks and last-route detection."""

from __future__ import annotations

from ydbdoc_review.validation.toc_targets import check_orphan_translated_pages


def test_F087_unrelated_orphan(tmp_path) -> None:
    root_toc = "ydb/docs/en/core/toc_p.yaml"
    scoped = "ydb/docs/en/core/changed.md"
    unrelated = "ydb/docs/en/core/unrelated-orphan.md"
    toc = "items:\n- name: Changed\n  href: changed.md\n"

    assert check_orphan_translated_pages(
        {scoped},
        repo_path=str(tmp_path),
        pending_toc_texts={root_toc: toc},
        pending_md_texts={scoped: "# Changed\n"},
    ) == {}
    assert unrelated not in check_orphan_translated_pages(
        {scoped},
        repo_path=str(tmp_path),
        pending_toc_texts={root_toc: toc},
        pending_md_texts={scoped: "# Changed\n"},
    )


def test_F087_last_route(tmp_path) -> None:
    root_toc = "ydb/docs/en/core/toc_p.yaml"
    affected = "ydb/docs/en/core/affected.md"

    orphans = check_orphan_translated_pages(
        {affected},
        repo_path=str(tmp_path),
        pending_toc_texts={root_toc: "items: []\n"},
        pending_md_texts={affected: "# Affected\n"},
    )

    assert affected in orphans
