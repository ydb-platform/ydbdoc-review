"""Tests for glossary unreachable-link stripping (YFM003 variant A)."""

from __future__ import annotations

from ydbdoc_review.validation.glossary_toc_links import (
    collect_en_toc_reachable_md,
    en_mirror_path,
    md_link_basenames_outside_reachable,
    resolve_internal_md_href,
    ru_toc_to_en_path,
    strip_unreachable_glossary_links,
    strip_unreachable_internal_links,
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

_RU_STREAMING_TOC = """\
items:
- name: Overview
  href: index.md
- name: Watermarks
  href: watermarks.md
"""


def test_resolve_internal_md_href_from_glossary():
    glossary = "ydb/docs/en/core/concepts/glossary.md"
    assert (
        resolve_internal_md_href(glossary, "datamodel/view.md")
        == "ydb/docs/en/core/concepts/datamodel/view.md"
    )
    assert (
        resolve_internal_md_href(
            "ydb/docs/ru/core/concepts/glossary.md",
            "./streaming-query/watermarks.md",
        )
        == "ydb/docs/en/core/concepts/streaming-query/watermarks.md"
    )
    assert resolve_internal_md_href(glossary, "#cluster") is None
    assert (
        resolve_internal_md_href(glossary, "https://en.wikipedia.org/wiki/RAID")
        is None
    )


def test_en_mirror_path():
    assert (
        en_mirror_path("ydb/docs/ru/core/concepts/glossary.md")
        == "ydb/docs/en/core/concepts/glossary.md"
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
        "ydb/docs/en/core/concepts/glossary.md": "# glossary\n",
        "ydb/docs/en/core/concepts/datamodel/view.md": "# view\n",
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


def test_collect_en_toc_reachable_md_does_not_use_ru_fallback_for_missing_en_toc():
    files = {
        "ydb/docs/en/core/toc_p.yaml": _MINI_TOC,
        "ydb/docs/en/core/concepts/toc_p.yaml": _CONCEPTS_TOC,
        "ydb/docs/ru/core/concepts/streaming-query/toc_p.yaml": _RU_STREAMING_TOC,
    }

    def read_text(path: str) -> str | None:
        return files.get(path)

    reachable = collect_en_toc_reachable_md(
        read_text,
        root_toc="ydb/docs/en/core/toc_p.yaml",
    )
    assert "ydb/docs/en/core/concepts/streaming-query/watermarks.md" not in reachable


def test_collect_en_toc_reachable_md_allows_ru_fallback_for_pending_toc():
    pending = "ydb/docs/en/core/concepts/query_execution/toc_i.yaml"
    files = {
        "ydb/docs/en/core/toc_p.yaml": _MINI_TOC,
        "ydb/docs/en/core/concepts/toc_p.yaml": _CONCEPTS_TOC,
        "ydb/docs/ru/core/concepts/query_execution/toc_i.yaml": """\
items:
- name: Process
  href: execution_process.md
""",
        "ydb/docs/en/core/concepts/query_execution/execution_process.md": "# process\n",
    }

    def read_text(path: str) -> str | None:
        return files.get(path)

    reachable = collect_en_toc_reachable_md(
        read_text,
        root_toc="ydb/docs/en/core/toc_p.yaml",
        extra_toc_paths=frozenset({pending}),
    )
    assert (
        "ydb/docs/en/core/concepts/query_execution/execution_process.md"
        in reachable
    )


def test_collect_en_toc_reachable_md_skips_missing_en_files():
    files = {
        "ydb/docs/en/core/toc_p.yaml": _MINI_TOC,
        "ydb/docs/en/core/concepts/toc_p.yaml": _CONCEPTS_TOC,
        "ydb/docs/en/core/dev/toc_p.yaml": """\
items:
- name: Fulltext indexes
  href: fulltext-indexes.md
- name: JSON indexes
  href: json-indexes.md
""",
        "ydb/docs/en/core/dev/fulltext-indexes.md": "# fulltext\n",
    }

    def read_text(path: str) -> str | None:
        return files.get(path)

    reachable = collect_en_toc_reachable_md(
        read_text,
        root_toc="ydb/docs/en/core/toc_p.yaml",
        extra_toc_paths=frozenset({"ydb/docs/en/core/dev/toc_p.yaml"}),
    )
    assert "ydb/docs/en/core/dev/fulltext-indexes.md" in reachable
    assert "ydb/docs/en/core/dev/json-indexes.md" not in reachable


def test_strip_unreachable_glossary_links():
    glossary_path = "ydb/docs/en/core/concepts/glossary.md"
    text = (
        "See [{#T}](datamodel/view.md) and "
        "[streaming query](streaming-query.md) for details."
    )
    reachable = frozenset({"ydb/docs/en/core/concepts/datamodel/view.md"})
    out = strip_unreachable_glossary_links(
        text,
        file_path=glossary_path,
        reachable=reachable,
    )
    assert "[{#T}](datamodel/view.md)" in out
    assert "streaming-query.md" not in out
    assert "streaming query" in out


def test_strip_unreachable_internal_links_case_44457_watermarks():
    text = (
        "More about watermarks in [{#T}](./streaming-query/watermarks.md).\n\n"
        "See [spring-ydb-retry](../integrations/spring/spring-retry.md)."
    )
    reachable = frozenset(
        {
            "ydb/docs/en/core/concepts/glossary.md",
            "ydb/docs/en/core/concepts/streaming-query.md",
        }
    )
    out = strip_unreachable_internal_links(
        text,
        file_path="ydb/docs/ru/core/concepts/glossary.md",
        reachable=reachable,
    )
    assert "watermarks.md" not in out
    assert "spring-retry.md" not in out
    assert "watermarks" in out
    assert "spring-ydb-retry" in out


def test_strip_unreachable_links_inside_table_cells():
    """Regression #39856: Table uses header/rows/cells, not .children (#46846 crash)."""
    text = (
        "| Topic | Link |\n"
        "| --- | --- |\n"
        "| W | [watermarks](watermarks.md) |\n"
        "| OK | [patterns](patterns.md) |\n"
    )
    reachable = frozenset(
        {"ydb/docs/en/core/dev/streaming-query/patterns.md"}
    )
    out = strip_unreachable_internal_links(
        text,
        file_path="ydb/docs/en/core/dev/streaming-query/index.md",
        reachable=reachable,
    )
    assert "watermarks.md" not in out
    assert "patterns.md" in out
    assert "watermarks" in out


def test_md_link_basenames_outside_reachable():
    text = (
        "See [watermarks](watermarks.md) and [patterns](patterns.md).\n"
    )
    reachable = frozenset(
        {"ydb/docs/en/core/concepts/streaming-query/patterns.md"}
    )
    ignore = md_link_basenames_outside_reachable(
        text,
        file_path="ydb/docs/ru/core/concepts/streaming-query/index.md",
        reachable=reachable,
    )
    assert ignore == {"watermarks.md"}
