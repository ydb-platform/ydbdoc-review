"""F-031 contract: navigation-only source changes stay in translation scope."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.navigation.redirects import (
    merge_en_redirects_yaml,
    parse_redirect_entries,
)
from ydbdoc_review.navigation.scope_planner import (
    navigation_pairs_from_plan,
    plan_translation_scope,
)
from ydbdoc_review.navigation.toc import (
    merge_en_toc_yaml,
    parse_toc_items,
    preserve_en_order_for_skipped_toc_entries,
)


def test_F031_nav_only() -> None:
    """TOC and redirects are executable inputs even without Markdown changes."""
    toc = """\
items:
- name: Обзор
  href: index.md
- name: Новая страница
  href: new.md
"""
    redirects = dedent(
        """
        - from: /old
          to: /new
        """
    ).strip()
    files = {
        "ydb/docs/ru/core/toc_i.yaml": toc,
        "ydb/docs/ru/redirects.yaml": redirects,
    }

    plan = plan_translation_scope(
        [
            ("ydb/docs/ru/core/toc_i.yaml", "modified"),
            ("ydb/docs/ru/redirects.yaml", "modified"),
        ],
        read_ru=files.get,
        read_en_base=lambda path: {
            "ydb/docs/en/core/toc_i.yaml": (
                "items:\n- name: Overview\n  href: index.md\n"
            ),
            "ydb/docs/en/redirects.yaml": (
                "- from: /old\n  to: /old-en\n"
            ),
        }.get(path),
        read_ru_base=lambda path: {
            "ydb/docs/ru/core/toc_i.yaml": (
                "items:\n- name: Обзор\n  href: index.md\n"
            ),
            "ydb/docs/ru/redirects.yaml": (
                "- from: /old\n  to: /old\n"
            ),
        }.get(path),
    )

    pairs = navigation_pairs_from_plan(plan)
    assert not plan.doc_ru_paths
    assert {pair.ru_path for pair in pairs} == {
        "ydb/docs/ru/core/toc_i.yaml",
        "ydb/docs/ru/redirects.yaml",
    }

    merged_toc = merge_en_toc_yaml(
        "items:\n- name: Overview\n  href: index.md\n",
        toc,
        translate_hrefs={"new.md"},
        translate_name=lambda name: {"Новая страница": "New page"}[name],
        ru_base_hrefs={"index.md"},
    )
    merged_redirects = merge_en_redirects_yaml(
        "- from: /old\n  to: /old-en\n",
        redirects,
        translate_from_paths={"/old"},
    )
    assert "href: new.md" in merged_toc
    assert "- from: /old\n  to: /new" in merged_redirects


def test_F031_unrelated_entries() -> None:
    """Scoped navigation updates preserve foreign and excluded entries/order."""
    ru_toc = dedent(
        """
        items:
        - name: Target RU
          href: target.md
        - name: Excluded RU
          href: excluded.md
        - name: Foreign RU
          href: foreign.md
        """
    ).strip()
    en_main = dedent(
        """
        items:
        - name: Foreign EN
          href: foreign.md
        - name: Excluded EN
          href: excluded.md
        - name: Target EN old
          href: target.md
        """
    ).strip()

    merged = merge_en_toc_yaml(
        en_main,
        ru_toc,
        translate_hrefs={"target.md"},
        translate_name=lambda name: "Target EN" if name == "Target RU" else name,
    )
    merged = preserve_en_order_for_skipped_toc_entries(
        en_main,
        merged,
        entry_is_skipped=lambda item: item.get("href") == "excluded.md",
    )
    items = parse_toc_items(merged)
    assert [item["href"] for item in items] == [
        "target.md",
        "excluded.md",
        "foreign.md",
    ]
    assert items[0]["name"] == "Target EN"
    assert items[1]["name"] == "Excluded EN"
    assert items[2]["name"] == "Foreign EN"

    merged_redirects = merge_en_redirects_yaml(
        dedent(
            """
            - from: /foreign
              to: /foreign-en
            - from: /target
              to: /target-old
            - from: /excluded
              to: /excluded-en
            """
        ).strip(),
        dedent(
            """
            - from: /target
              to: /target-new
            - from: /excluded
              to: /excluded-new
            """
        ).strip(),
        translate_from_paths={"/target"},
    )
    redirect_items = parse_redirect_entries(merged_redirects)
    assert [(item["from_path"], item["to_path"]) for item in redirect_items] == [
        ("/target", "/target-new"),
        ("/excluded", "/excluded-en"),
        ("/foreign", "/foreign-en"),
    ]
