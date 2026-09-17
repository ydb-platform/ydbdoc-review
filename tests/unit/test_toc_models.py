"""Tests for additive TOC merge models (§6.131)."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.navigation.toc import toc_translate_scope
from ydbdoc_review.navigation.toc_models import (
    build_toc_entry_mappings,
    build_toc_merge_scope,
    mapping_covers_ru_href,
    severity_for_kind,
)


def test_build_toc_merge_scope_splits_added_and_modified():
    ru_base = dedent("""
        items:
        - name: Старый
          href: old.md
        - name: Индекс
          href: index.md
    """).strip()
    ru_pr = dedent("""
        items:
        - name: Старый переименован
          href: old.md
        - name: Новый
          href: new.md
        - name: Индекс
          href: index.md
    """).strip()
    scope = build_toc_merge_scope(ru_base, ru_pr)
    assert scope.added_hrefs == frozenset({"new.md"})
    assert scope.modified_hrefs == frozenset({"old.md"})
    assert "index.md" not in scope.added_hrefs | scope.modified_hrefs
    # Facade matches classic translate scope
    classic = toc_translate_scope(ru_base, ru_pr)
    assert scope.to_translate_scope().hrefs == classic.hrefs


def test_build_toc_merge_scope_tracks_removed():
    ru_base = dedent("""
        items:
        - name: A
          href: a.md
        - name: B
          href: b.md
    """).strip()
    ru_pr = dedent("""
        items:
        - name: A
          href: a.md
    """).strip()
    scope = build_toc_merge_scope(ru_base, ru_pr)
    assert scope.removed_hrefs == frozenset({"b.md"})


def test_toc_entry_mapping_exact_and_legacy_alias():
    ru_items = [
        {"name": "Hive", "href": "hive_config.md"},
        {"name": "Other", "href": "other.md"},
    ]
    en_items = [
        {"name": "Hive", "href": "hive.md"},
        {"name": "Other", "href": "other.md"},
    ]
    mappings = build_toc_entry_mappings(
        ru_items, en_items, en_main_hrefs={"hive.md", "other.md"}
    )
    by_ru = {m.ru_href: m for m in mappings}
    assert by_ru["other.md"].en_href == "other.md"
    assert by_ru["other.md"].legacy_aliases == frozenset()
    assert by_ru["hive_config.md"].en_href == "hive.md"
    assert "hive.md" in by_ru["hive_config.md"].legacy_aliases
    assert mapping_covers_ru_href(mappings, "hive_config.md")
    assert mapping_covers_ru_href(mappings, "other.md")
    assert not mapping_covers_ru_href(mappings, "missing.md")


def test_severity_for_known_kinds():
    assert severity_for_kind("toc_structure_parity") == "BLOCKING"
    assert severity_for_kind("toc_en_only_legacy") == "WARNING"
    assert severity_for_kind("scope_not_applied") == "ERROR"
