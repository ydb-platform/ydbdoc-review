"""Tests for toc.yaml scoped merge."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.navigation.toc import (
    merge_en_toc_yaml,
    parse_toc_items,
    toc_translate_scope,
    validate_toc_merge,
)

RU_BASE = dedent("""
    items:
    - name: Старый раздел
      href: old.md
    - name: Без изменений
      href: stable.md
""").strip()

RU_PR = dedent("""
    items:
    - name: Старый раздел (переименован)
      href: old.md
    - name: Без изменений
      href: stable.md
    - name: Новый раздел
      href: new-page.md
""").strip()

EN_MAIN = dedent("""
    items:
    - name: Old section
      href: old.md
    - name: Unchanged
      href: stable.md
    - name: EN legacy only
      href: legacy.md
""").strip()


def test_parse_toc_items():
    items = parse_toc_items(RU_PR)
    assert len(items) == 3
    assert items[0]["href"] == "old.md"
    assert items[2]["name"] == "Новый раздел"


def test_toc_translate_scope_detects_new_and_renamed():
    scope = toc_translate_scope(RU_BASE, RU_PR)
    assert scope == {"old.md", "new-page.md"}
    assert "stable.md" not in scope


def test_merge_keeps_unchanged_en_labels():
    scope = toc_translate_scope(RU_BASE, RU_PR)

    def fake_translate(name: str) -> str:
        return {"Старый раздел (переименован)": "Old section (renamed)", "Новый раздел": "New section"}[
            name
        ]

    merged = merge_en_toc_yaml(
        EN_MAIN,
        RU_PR,
        translate_hrefs=scope,
        translate_name=fake_translate,
    )
    items = parse_toc_items(merged)
    by_href = {it["href"]: it["name"] for it in items}

    assert by_href["stable.md"] == "Unchanged"
    assert by_href["old.md"] == "Old section (renamed)"
    assert by_href["new-page.md"] == "New section"
    assert by_href["legacy.md"] == "EN legacy only"


def test_merge_skips_ru_only_not_in_scope():
    """RU added new-page.md but it's not in translate_hrefs → not added to EN."""
    merged = merge_en_toc_yaml(
        EN_MAIN,
        RU_PR,
        translate_hrefs={"old.md"},
        translate_name=lambda n: "X",
    )
    hrefs = {it["href"] for it in parse_toc_items(merged)}
    assert "new-page.md" not in hrefs
    assert "old.md" in hrefs


def test_validate_toc_merge_clean():
    scope = toc_translate_scope(RU_BASE, RU_PR)
    merged = merge_en_toc_yaml(
        EN_MAIN,
        RU_PR,
        translate_hrefs=scope,
        translate_name=lambda n: "T",
    )
    issues = validate_toc_merge(RU_PR, merged, translate_hrefs=scope, en_main_yaml=EN_MAIN)
    assert not issues
