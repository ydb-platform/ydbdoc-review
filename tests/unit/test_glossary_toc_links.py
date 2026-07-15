"""Tests for glossary unreachable-link stripping (YFM003 variant A)."""

from __future__ import annotations

from ydbdoc_review.validation.glossary_toc_links import (
    collect_en_toc_reachable_md,
    resolve_internal_md_href,
    ru_toc_to_en_path,
    strip_unreachable_glossary_links,
)

_MINI_TOC = """\
title: Core
items:
- name: Glossary
  href: concepts/glossary.md
- include:
    path: concepts/toc_p.yaml
"""

_CONCEPTS_TOC = """\
items:
- name: View
  href: datamodel/view.md
"""


def test_resolve_internal_md_href_from_glossary():
    glossary = "ydb/docs/en/core/concepts/glossary.md"
    assert (
        resolve_internal_md_href(glossary, "datamodel/view.md")
        == "ydb/docs/en/core/concepts/datamodel/view.md"
    )
    assert resolve_internal_md_href(glossary, "#cluster") is None
    assert (
        resolve_internal_md_href(glossary, "https://en.wikipedia.org/wiki/RAID")
        is None
    )


def test_ru_toc_to_en_path():
    assert (
        ru_toc_to_en_path("ydb/docs/ru/core/concepts/query_execution/toc_i.yaml")
        == "ydb/docs/en/core/concepts/query_execution/toc_i.yaml"
    )


def test_collect_en_toc_reachable_md_bfs():
    files = {
        "ydb/docs/en/core/toc_p.yaml": _MINI_TOC,
        "ydb/docs/en/core/concepts/toc_p.yaml": _CONCEPTS_TOC,
    }

    def read_text(path: str) -> str | None:
        return files.get(path)

    reachable = collect_en_toc_reachable_md(
        read_text,
        root_toc="ydb/docs/en/core/toc_p.yaml",
        extra_md_paths=frozenset(),
        extra_toc_paths=frozenset(),
    )
    assert "ydb/docs/en/core/concepts/glossary.md" in reachable
    assert "ydb/docs/en/core/concepts/datamodel/view.md" in reachable


def test_strip_unreachable_glossary_links():
    glossary_path = "ydb/docs/en/core/concepts/glossary.md"
    text = (
        "See [{#T}](datamodel/view.md) and "
        "[streaming query](streaming-query.md) for details."
    )
    reachable = frozenset(
        {"ydb/docs/en/core/concepts/datamodel/view.md"}
    )
    out = strip_unreachable_glossary_links(
        text,
        file_path=glossary_path,
        reachable=reachable,
    )
    assert "[{#T}](datamodel/view.md)" in out
    assert "streaming-query.md" not in out
    assert "streaming query" in out
