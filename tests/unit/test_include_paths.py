"""Tests for locale include path resolution."""

from __future__ import annotations

from ydbdoc_review.parsing.include_paths import (
    collect_yfm_includes,
    resolve_locale_md_path,
)


def test_resolve_sibling_include_in_locale_includes_dir():
    base = (
        "ydb/docs/ru/core/reference/ydb-cli/export-import/"
        "_includes/export-s3.md"
    )
    resolved = resolve_locale_md_path(
        base, "export-additional-params.md", docs_root="ydb/docs"
    )
    assert resolved == (
        "ydb/docs/ru/core/reference/ydb-cli/export-import/"
        "_includes/export-additional-params.md"
    )


def test_resolve_include_from_page_via_includes_subdir():
    base = "ydb/docs/ru/core/reference/ydb-cli/export-import/export-nfs.md"
    resolved = resolve_locale_md_path(
        base, "_includes/export-additional-params.md", docs_root="ydb/docs"
    )
    assert resolved == (
        "ydb/docs/ru/core/reference/ydb-cli/export-import/"
        "_includes/export-additional-params.md"
    )


def test_skip_repo_root_neutral_include():
    base = "ydb/docs/ru/core/page.md"
    resolved = resolve_locale_md_path(
        base, "/ydb/docs/_includes/auth.md", docs_root="ydb/docs"
    )
    assert resolved is None


def test_collect_yfm_includes_from_markdown():
    text = (
        "Intro.\n\n"
        "{% include [frag](child.md) %}\n\n"
        "After.\n"
    )
    includes = collect_yfm_includes(text)
    assert len(includes) == 1
    assert includes[0].path == "child.md"
