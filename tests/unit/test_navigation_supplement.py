"""Tests for parent toc supplementation after translating new EN pages."""

from __future__ import annotations

from textwrap import dedent
from unittest.mock import patch

from ydbdoc_review.pipeline.navigation_supplement import supplement_navigation_pairs
from ydbdoc_review.pipeline.pairs import NavigationPair

RU_CONFIG_TOC = dedent("""
    items:
    - name: actor_system_config
      href: actor_system_config.md
    - name: system_tablet_backup_config
      href: system_tablet_backup_config.md
""").strip()

EN_CONFIG_TOC_MAIN = dedent("""
    items:
    - name: actor_system_config
      href: actor_system_config.md
""").strip()

EN_MD = (
    "ydb/docs/en/core/reference/configuration/system_tablet_backup_config.md"
)
RU_TOC = "ydb/docs/ru/core/reference/configuration/toc_p.yaml"
EN_TOC = "ydb/docs/en/core/reference/configuration/toc_p.yaml"


def test_supplement_adds_parent_toc_when_ru_main_has_href_en_main_lacks():
    def _read(repo: str, path: str) -> str | None:
        if path == RU_TOC:
            return RU_CONFIG_TOC
        return None

    def _read_ref(repo: str, ref: str, path: str) -> str | None:
        if path == EN_TOC and ref in ("abc123", "origin/main"):
            return EN_CONFIG_TOC_MAIN
        return None

    with (
        patch(
            "ydbdoc_review.pipeline.navigation_supplement.merge_base",
            return_value="abc123",
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_supplement.read_text",
            side_effect=_read,
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_supplement.read_text_at_ref",
            side_effect=_read_ref,
        ),
    ):
        out = supplement_navigation_pairs(
            [],
            {EN_MD},
            repo_path="/tmp/repo",
            merge_base_with="origin/main",
        )

    assert len(out) == 1
    assert out[0] == NavigationPair(
        ru_path=RU_TOC,
        en_path=EN_TOC,
        ru_changed=True,
        supplement_only=True,
    )


def test_supplement_skips_when_en_main_already_has_href():
    en_full = RU_CONFIG_TOC

    def _read(repo: str, path: str) -> str | None:
        if path == RU_TOC:
            return RU_CONFIG_TOC
        return None

    def _read_ref(repo: str, ref: str, path: str) -> str | None:
        if path == EN_TOC:
            return en_full
        return None

    with (
        patch(
            "ydbdoc_review.pipeline.navigation_supplement.merge_base",
            return_value="abc123",
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_supplement.read_text",
            side_effect=_read,
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_supplement.read_text_at_ref",
            side_effect=_read_ref,
        ),
    ):
        out = supplement_navigation_pairs(
            [],
            {EN_MD},
            repo_path="/tmp/repo",
            merge_base_with="origin/main",
        )

    assert out == []


def test_supplement_does_not_duplicate_existing_pair():
    existing = [
        NavigationPair(
            ru_path=RU_TOC,
            en_path=EN_TOC,
            ru_changed=True,
        )
    ]

    def _read(repo: str, path: str) -> str | None:
        if path == RU_TOC:
            return RU_CONFIG_TOC
        return None

    def _read_ref(repo: str, ref: str, path: str) -> str | None:
        if path == EN_TOC:
            return EN_CONFIG_TOC_MAIN
        return None

    with (
        patch(
            "ydbdoc_review.pipeline.navigation_supplement.merge_base",
            return_value="abc123",
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_supplement.read_text",
            side_effect=_read,
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_supplement.read_text_at_ref",
            side_effect=_read_ref,
        ),
    ):
        out = supplement_navigation_pairs(
            existing,
            {EN_MD},
            repo_path="/tmp/repo",
            merge_base_with="origin/main",
        )

    assert out == existing
