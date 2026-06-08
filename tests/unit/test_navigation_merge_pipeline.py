"""Tests for navigation merge in doc_translate pipeline."""

from __future__ import annotations

from textwrap import dedent
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.navigation_merge import (
    extra_toc_hrefs_from_md_targets,
    merge_navigation_pair,
    verify_navigation_pair,
)
from ydbdoc_review.pipeline.pairs import NavigationPair
from ydbdoc_review.translation.glossary import load_glossary

RU_BASE = dedent("""
    items:
    - { name: Обзор,      href: index.md                                          }
    - { name: FAMILY,     href: family.md,          when: backend_name == "YDB"   }
""").strip()

RU_PR = dedent("""
    items:
    - { name: Обзор,      href: index.md                                          }
    - { name: FAMILY,     href: family.md,          when: backend_name == "YDB"   }
    - { name: COMPACT,    href: compact.md,         when: backend_name == "YDB"   }
""").strip()

EN_MAIN = dedent("""
    items:
     - { name: Overview,    href: index.md                                          }
     - { name: FAMILY,      href: family.md                                         }
""").strip()


def test_merge_navigation_pair_inline_toc():
    client = MagicMock()
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    glossary = load_glossary()
    pair = NavigationPair(
        ru_path="ydb/docs/ru/core/alter_table/toc_i.yaml",
        en_path="ydb/docs/en/core/alter_table/toc_i.yaml",
        ru_changed=True,
    )

    with (
        patch(
            "ydbdoc_review.pipeline.navigation_merge.read_text",
            return_value=RU_PR,
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._read_at_base",
            side_effect=[RU_BASE, EN_MAIN],
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._translate_menu_labels",
            return_value={"COMPACT": "COMPACT"},
        ),
    ):
        result = merge_navigation_pair(
            pair,
            repo_path="/tmp/repo",
            merge_base_with="origin/main",
            client=client,
            glossary=glossary,
            config=cfg,
            extra_toc_hrefs={"compact.md"},
        )

    assert result.error is None
    assert result.target_text is not None
    assert result.verdict == "ok"
    assert "compact.md" in result.target_text
    assert "COMPACT" in result.target_text
    assert "Overview" in result.target_text
    assert "FAMILY" in result.target_text
    for line in result.target_text.splitlines():
        if line.strip().startswith("- {"):
            assert line.startswith(" - {"), line


def test_extra_toc_hrefs_from_md_targets_skips_locale_includes():
    paths = {
        "ydb/docs/en/core/integrations/orm/exposed.md",
        "ydb/docs/en/core/integrations/orm/_includes/toc-table.md",
    }
    assert extra_toc_hrefs_from_md_targets(paths) == {"exposed.md"}


ORM_RU_BASE = dedent("""
    items:
    - { name: Hibernate, href: hibernate.md }
    - { name: Django, href: django.md }
""").strip()

ORM_RU_PR = dedent("""
    items:
    - { name: Hibernate, href: hibernate.md }
    - { name: Django, href: django.md }
    - { name: Kotlin Exposed, href: exposed.md }
""").strip()

ORM_EN_MAIN = ORM_RU_BASE
ORM_EN_OK = ORM_RU_PR


def test_verify_orm_toc_ok_when_include_translated_not_in_sidebar():
    """Regression: PR #42768 — toc-table.md must not be required in toc-orm.yaml."""
    pair = NavigationPair(
        ru_path="ydb/docs/ru/core/integrations/orm/toc-orm.yaml",
        en_path="ydb/docs/en/core/integrations/orm/toc-orm.yaml",
        en_changed=True,
    )
    md_paths = {
        "ydb/docs/en/core/integrations/orm/exposed.md",
        "ydb/docs/en/core/integrations/orm/_includes/toc-table.md",
    }
    result = verify_navigation_pair(
        pair,
        ru_pr=ORM_RU_PR,
        en_text=ORM_EN_OK,
        ru_base=ORM_RU_BASE,
        en_main=ORM_EN_MAIN,
        extra_toc_hrefs=extra_toc_hrefs_from_md_targets(md_paths),
    )
    assert result.verdict == "ok"
    assert not any("toc-table.md" in w for w in result.warnings)
