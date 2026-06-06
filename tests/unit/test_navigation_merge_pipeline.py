"""Tests for navigation merge in doc_translate pipeline."""

from __future__ import annotations

from textwrap import dedent
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.navigation_merge import merge_navigation_pair
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
