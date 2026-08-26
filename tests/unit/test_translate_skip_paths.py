"""§6.167: skip translating public-materials/*."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.navigation.toc import (
    merge_en_toc_yaml,
    preserve_en_order_for_skipped_toc_entries,
)
from ydbdoc_review.pipeline.skip_paths import (
    filter_translate_changes,
    matches_translate_skip,
    toc_entry_is_skipped,
)

GLOBS = [
    "**/public-materials/**",
    "public-materials/**",
    "**/guide-to-public-material.md",
    "guide-to-public-material.md",
]


def test_matches_public_materials_repo_and_relative():
    assert matches_translate_skip(
        "ydb/docs/ru/core/public-materials/videos.md", GLOBS
    )
    assert matches_translate_skip("public-materials/toc_p.yaml", GLOBS)
    assert matches_translate_skip(
        "ydb/docs/en/core/public-materials/toc_p.yaml", GLOBS
    )
    assert matches_translate_skip(
        "ydb/docs/ru/core/contributor/documentation/guide-to-public-material.md",
        GLOBS,
    )
    assert matches_translate_skip("guide-to-public-material.md", GLOBS)
    assert not matches_translate_skip("ydb/docs/ru/core/toc_i.yaml", GLOBS)
    assert not matches_translate_skip(
        "ydb/docs/ru/core/contributor/documentation/style-guide.md",
        GLOBS,
    )


def test_filter_translate_changes_drops_public_materials():
    changes = [
        ("ydb/docs/ru/core/toc_i.yaml", "modified"),
        ("ydb/docs/ru/core/public-materials/videos.md", "modified"),
        (
            "ydb/docs/ru/core/contributor/documentation/guide-to-public-material.md",
            "added",
        ),
    ]
    out = filter_translate_changes(changes, GLOBS)
    assert [p for p, _ in out] == [
        "ydb/docs/ru/core/toc_i.yaml",
    ]


def test_preserve_en_order_keeps_public_materials_slot():
    """#48411: RU moved Public materials after Downloads — EN must not follow."""
    en_main = dedent(
        """\
        items:
        - name: FAQ
          include:
            mode: link
            path: faq/toc_p.yaml
        - name: Public materials
          include:
            mode: link
            path: public-materials/toc_p.yaml
        - name: Downloads
          href: downloads/index.md
          include:
            mode: link
            path: downloads/toc_i.yaml
        - name: Changelog
          include:
            mode: link
            path: changelog/toc_p.yaml
        """
    )
    ru_pr = dedent(
        """\
        items:
        - name: FAQ
          include:
            mode: link
            path: faq/toc_p.yaml
        - name: Downloads
          href: downloads/index.md
          include:
            mode: link
            path: downloads/toc_i.yaml
        - name: Public materials
          include:
            mode: link
            path: public-materials/toc_p.yaml
        - name: Changelog
          include:
            mode: link
            path: changelog/toc_p.yaml
        """
    )
    merged = merge_en_toc_yaml(
        en_main,
        ru_pr,
        translate_hrefs=set(),
        translate_name=lambda n: n,
        translate_include_paths=set(),
    )
    # Without preserve, Public materials follows RU (after Downloads).
    assert "path: downloads/toc_i.yaml" in merged
    preserved = preserve_en_order_for_skipped_toc_entries(
        en_main,
        merged,
        entry_is_skipped=lambda it: toc_entry_is_skipped(it, GLOBS),
    )
    # Public materials stays before Downloads (EN main order).
    pub = preserved.index("public-materials/toc_p.yaml")
    dl = preserved.index("downloads/toc_i.yaml")
    assert pub < dl
    assert preserved.strip() == en_main.strip()
