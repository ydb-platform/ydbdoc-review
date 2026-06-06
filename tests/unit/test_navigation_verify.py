"""Tests for doc_verify navigation validation."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.pipeline.navigation_merge import verify_navigation_pair
from ydbdoc_review.pipeline.pairs import NavigationPair

RU_BASE = dedent("""
    items:
    - { name: FAMILY, href: family.md, when: backend_name == "YDB" }
""").strip()

RU_PR = dedent("""
    items:
    - { name: Обзор, href: index.md }
    - { name: FAMILY, href: family.md, when: backend_name == "YDB" }
    - { name: COMPACT, href: compact.md, when: backend_name == "YDB" }
""").strip()

EN_MAIN = dedent("""
    items:
     - { name: Overview, href: index.md }
     - { name: FAMILY, href: family.md }
""").strip()

EN_OK = dedent("""
    items:
     - { name: Overview, href: index.md }
     - { name: FAMILY, href: family.md }
     - { name: COMPACT, href: compact.md, when: backend_name == "YDB" }
""").strip()


def test_verify_navigation_pair_ok():
    pair = NavigationPair(
        ru_path="ydb/docs/ru/core/alter_table/toc_i.yaml",
        en_path="ydb/docs/en/core/alter_table/toc_i.yaml",
        en_changed=True,
    )
    result = verify_navigation_pair(
        pair,
        ru_pr=RU_PR,
        en_text=EN_OK,
        ru_base=RU_BASE,
        en_main=EN_MAIN,
        extra_toc_hrefs={"compact.md"},
    )
    assert result.error is None
    assert result.verdict == "ok"
    assert result.target_text is None


def test_verify_navigation_pair_blocked_on_empty_toc():
    pair = NavigationPair(
        ru_path="ydb/docs/ru/core/alter_table/toc_i.yaml",
        en_path="ydb/docs/en/core/alter_table/toc_i.yaml",
        en_changed=True,
    )
    result = verify_navigation_pair(
        pair,
        ru_pr=RU_PR,
        en_text="items:\n",
        ru_base=RU_BASE,
        en_main=EN_MAIN,
        extra_toc_hrefs={"compact.md", "index.md"},
    )
    assert result.verdict == "blocked"
    assert any("empty_toc" in w for w in result.warnings)
