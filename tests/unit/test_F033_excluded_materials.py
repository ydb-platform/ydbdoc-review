"""F-033: public materials are immutable in every translation operation."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.navigation.toc import (
    merge_en_toc_yaml,
    preserve_en_order_for_skipped_toc_entries,
)
from ydbdoc_review.pipeline.skip_paths import (
    filter_path_set,
    filter_translate_changes,
    toc_entry_is_skipped,
)


def test_F033_all_operations():
    globs = load_config(env={}).paths.translate_skip_globs
    changes = [
        ("ydb/docs/ru/core/public-materials/page.md", "modified"),
        ("ydb/docs/ru/core/public-materials/page.md", "deleted"),
        ("ydb/docs/ru/core/public-materials/renamed.md", "added"),
        ("ydb/docs/ru/core/public-materials/image.png", "modified"),
        ("ydb/docs/ru/core/public-materials/page.md#anchor", "modified"),
        ("ydb/docs/ru/core/public-materials/linked.md", "modified"),
        ("ydb/docs/ru/core/contributor/documentation/guide-to-public-material.md", "modified"),
        ("ydb/docs/ru/core/ordinary.md", "modified"),
    ]

    assert filter_translate_changes(changes, globs) == [
        ("ydb/docs/ru/core/ordinary.md", "modified"),
    ]
    assert filter_path_set({path for path, _ in changes}, globs) == {
        "ydb/docs/ru/core/ordinary.md",
    }


def test_F033_toc_continue():
    globs = load_config(env={}).paths.translate_skip_globs
    en_parent = dedent(
        """\
        items:
        - name: Public materials
          include:
            mode: link
            path: public-materials/toc_p.yaml
        - name: Ordinary
          href: ordinary.md
        """
    )
    ru_parent_after_continue = dedent(
        """\
        items:
        - name: Ordinary translated
          href: ordinary.md
        - name: Public materials translated by continue
          include:
            mode: link
            path: public-materials/toc_p.yaml
        """
    )
    merged = merge_en_toc_yaml(
        en_parent,
        ru_parent_after_continue,
        translate_hrefs={"ordinary.md"},
        translate_name=lambda name: name.upper(),
        translate_include_paths={"public-materials/toc_p.yaml"},
    )
    preserved = preserve_en_order_for_skipped_toc_entries(
        en_parent,
        merged,
        entry_is_skipped=lambda item: toc_entry_is_skipped(item, globs),
    )

    assert "- name: Public materials\n  include:\n    mode: link\n    path: public-materials/toc_p.yaml" in preserved
    assert preserved.index("public-materials/toc_p.yaml") < preserved.index("ordinary.md")
    assert filter_translate_changes(
        [("ydb/docs/ru/core/public-materials/toc_p.yaml", "modified")],
        globs,
    ) == []
