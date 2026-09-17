"""F-081: standalone pages must be rooted in the locale TOC graph."""

from __future__ import annotations

from ydbdoc_review.navigation.redirects import (
    redirect_source_repo_md_paths,
    should_skip_redirect_tombstone_en,
)
from ydbdoc_review.validation.toc_targets import check_orphan_pages_for_locale

ROOT = "ydb/docs"
TOC = f"{ROOT}/en/core/toc_p.yaml"
ROOT_PAGE = f"{ROOT}/en/core/guide/root.md"
NAV_PAGE = f"{ROOT}/en/core/guide/affected.md"
LINK_ONLY = f"{ROOT}/en/core/guide/link-only.md"
REDIRECTED = f"{ROOT}/en/core/guide/redirected.md"
REDIRECTS = "common:\n  - from: /guide/redirected.md\n    to: /guide/live.md\n"


def test_F081_root_path(tmp_path) -> None:
    """Root TOC reachability covers both translated and nav-affected pages."""
    assert check_orphan_pages_for_locale(
        {ROOT_PAGE, NAV_PAGE},
        repo_path=str(tmp_path),
        pending_toc_texts={
            TOC: (
                "items:\n"
                "- name: Root\n  href: guide/root.md\n"
                "- name: Affected\n  href: guide/affected.md\n"
            )
        },
        pending_md_texts={ROOT_PAGE: "# Root\n", NAV_PAGE: "# Affected\n"},
    ) == {}


def test_F081_link_only(tmp_path) -> None:
    """A Markdown-only inbound link is not TOC reachability; a redirect is."""
    orphans = check_orphan_pages_for_locale(
        {LINK_ONLY},
        repo_path=str(tmp_path),
        pending_toc_texts={TOC: "items:\n- name: Root\n  href: guide/root.md\n"},
        pending_md_texts={LINK_ONLY: "# Link only\n"},
    )
    assert LINK_ONLY in orphans

    redirect_sources = redirect_source_repo_md_paths(REDIRECTS, locale="en")
    assert REDIRECTED in redirect_sources
    assert should_skip_redirect_tombstone_en(
        REDIRECTED,
        redirect_source_en_paths=redirect_sources,
    )
