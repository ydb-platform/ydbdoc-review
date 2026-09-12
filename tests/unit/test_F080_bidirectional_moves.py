"""F-080: bidirectional moves retain paired routes and raw-link symmetry."""

from __future__ import annotations

from ydbdoc_review.navigation.redirects import (
    follow_redirect_repo_md_path,
    merge_en_redirects_yaml,
)
from ydbdoc_review.validation.href_parity import check_href_parity

ROOT = "ydb/docs"
OLD_RU = f"{ROOT}/ru/core/guide/old.md"
OLD_EN = f"{ROOT}/en/core/guide/old.md"
NEW_RU = f"{ROOT}/ru/core/guide/new.md"
NEW_EN = f"{ROOT}/en/core/guide/new.md"
REDIRECTS = "common:\n  - from: /guide/old.md\n    to: /guide/new.md\n"


def test_F080_both_directions() -> None:
    """The same public move remains resolvable in both locale trees."""
    assert follow_redirect_repo_md_path(OLD_RU, REDIRECTS) == NEW_RU
    assert follow_redirect_repo_md_path(OLD_EN, REDIRECTS) == NEW_EN

    merged = merge_en_redirects_yaml(
        "- from: /guide/old.md\n  to: /guide/new.md\n",
        "- from: /guide/old.md\n  to: /guide/new.md\n",
        translate_from_paths=set(),
    )
    assert "- from: /guide/old.md" in merged
    assert "  to: /guide/new.md" in merged


def test_F080_old_vs_final() -> None:
    """A/A is clean; A/B is not made symmetric by a shared final target."""
    source = "See [page](../guide/old.md#topic).\n"
    assert check_href_parity(source, source, en_baseline_text=source) == []

    final_path = "See [page](../guide/new.md#topic).\n"
    issues = check_href_parity(source, final_path, en_baseline_text=source)
    assert issues
    assert "href_parity" in issues[0]
