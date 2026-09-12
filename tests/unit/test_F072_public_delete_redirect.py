"""F-072: public deletions require a live redirect."""

from __future__ import annotations

from ydbdoc_review.navigation.redirects import (
    follow_redirect_repo_md_path,
    should_skip_redirect_tombstone_en,
    validate_redirect_merge,
)
from ydbdoc_review.validation.en_link_targets import check_en_page_link_targets

DOCS_ROOT = "ydb/docs"
OLD = f"{DOCS_ROOT}/en/core/guide/old.md"
NEW = f"{DOCS_ROOT}/en/core/guide/new.md"
PAGE = f"{DOCS_ROOT}/en/core/guide/referrer.md"

REDIRECTS = """common:
  - from: /guide/old.md
    to: /guide/new.md
"""


def test_F072_page_resource() -> None:
    """A deleted page/resource without a redirect remains in the required scope."""
    issues = validate_redirect_merge(
        "",
        "",
        translate_from_paths={"/guide/old.md", "/assets/old-schema.json"},
        en_main_yaml="",
    )
    assert {issue.kind for issue in issues} == {"scope_not_applied"}
    assert not should_skip_redirect_tombstone_en(
        OLD,
        redirect_source_en_paths=set(),
    )
    assert follow_redirect_repo_md_path(OLD, "") == OLD


def test_F072_working_redirect() -> None:
    """A live redirect permits deletion and keeps the old fragment addressable."""
    assert should_skip_redirect_tombstone_en(
        OLD,
        redirect_source_en_paths={OLD},
    )
    assert follow_redirect_repo_md_path(OLD, REDIRECTS) == NEW

    final_tree = {NEW: "## Replacement {#kept-fragment}\n"}
    old_href = "See [old section](old.md#kept-fragment).\n"

    def baseline_read(path: str) -> str | None:
        if path.endswith("redirects.yaml"):
            return REDIRECTS
        return final_tree.get(path)

    assert check_en_page_link_targets(
        PAGE,
        old_href,
        read_text=final_tree.get,
        baseline_read_text=baseline_read,
    ) == []
