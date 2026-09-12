"""F-075: redirect resolution is finite, local, and evidence based."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.navigation.link_deps import canonical_md_dependency_path
from ydbdoc_review.navigation.redirects import (
    follow_redirect_repo_md_path,
    prefix_redirect_repo_md_path,
)
from ydbdoc_review.validation.toc_targets import check_orphan_pages_for_locale

ROOT = "ydb/docs"
OLD_EN = f"{ROOT}/en/core/guide/legacy.md"
OLD_RU = f"{ROOT}/ru/core/guide/legacy.md"
LIVE_EN = f"{ROOT}/en/core/guide/current.md"


def test_F075_chains(tmp_path) -> None:
    redirects = dedent(
        """
        common:
          - from: /guide/legacy.md
            to: /guide/old.md
          - from: /guide/old.md
            to: /guide/current.md
        """
    )

    assert canonical_md_dependency_path(OLD_EN, redirects_yaml=redirects) == LIVE_EN
    assert follow_redirect_repo_md_path(OLD_RU, redirects) == (
        f"{ROOT}/ru/core/guide/old.md"
    )

    root_toc = f"{ROOT}/en/core/toc_p.yaml"
    assert check_orphan_pages_for_locale(
        {LIVE_EN},
        repo_path=str(tmp_path),
        pending_toc_texts={
            root_toc: "items:\n- name: Current\n  href: guide/current.md\n"
        },
        pending_md_texts={LIVE_EN: "# Current\n"},
    ) == {}

    self_loop = "common:\n  - from: /guide/legacy.md\n    to: /guide/legacy.md\n"
    assert canonical_md_dependency_path(OLD_EN, redirects_yaml=self_loop) is None

    cycle = dedent(
        """
        common:
          - from: /guide/legacy.md
            to: /guide/old.md
          - from: /guide/old.md
            to: /guide/legacy.md
        """
    )
    assert canonical_md_dependency_path(OLD_EN, redirects_yaml=cycle) is None

    conflicting_prefixes = dedent(
        """
        common:
          - from: /guide/(.*)$
            to: /one/$1
          - from: /guide/(.*)$
            to: /two/$1
        """
    )
    assert prefix_redirect_repo_md_path(OLD_EN, conflicting_prefixes) is None


def test_F075_prefix_rules() -> None:
    prefixes = dedent(
        """
        common:
          - from: /legacy/(.*)$
            to: /current/$1
        """
    )
    affected = f"{ROOT}/en/core/legacy/section/page.md"
    assert prefix_redirect_repo_md_path(affected, prefixes) == (
        f"{ROOT}/en/core/current/section/page.md"
    )

    unknown = "common:\n  - from: /unknown/(.*)$\n    to: /guessed/$1\n"
    unrelated = f"{ROOT}/en/core/legacy/section/page.md"
    assert prefix_redirect_repo_md_path(unrelated, unknown) == unrelated
    assert canonical_md_dependency_path(unrelated, redirects_yaml=unknown) == unrelated
