"""Deterministic EN postprocess after segment translate."""

from ydbdoc_review.markdown_links import (
    fix_bare_relative_md_paths_from_ru,
    fix_bare_urls_in_prose,
    fix_broken_anchor_links,
    restore_markdown_links_from_ru,
)
from ydbdoc_review.markdown_link_paths import extract_relative_link_refs
from ydbdoc_review.translate_postprocess import (
    fix_heading_anchors_from_ru,
    fix_list_tabs_markdown_layout,
    fix_wikipedia_links_for_en,
)


def test_fix_wikipedia_links_for_en():
    text = "|[Snappy](https://ru.wikipedia.org/wiki/Snappy_(библиотека))|✓|"
    out = fix_wikipedia_links_for_en(text)
    assert "en.wikipedia.org" in out
    assert "библиотека" not in out
    assert "Snappy_(library)" in out


def test_fix_list_tabs_markdown_layout():
    glued = "## Start static nodes {#start-storage}{% list tabs group=manual-systemd %}"
    out = fix_list_tabs_markdown_layout(glued)
    assert "## Start static nodes {#start-storage}\n\n{% list tabs" in out
    assert "{#start-storage}{%" not in out


def test_fix_heading_anchors_from_ru():
    ru = "### Формат json_each_row {#json_each_row}\n"
    en = "### json_each_row format {#tsv_with_names}\n"
    out = fix_heading_anchors_from_ru(ru, en)
    assert "{#json_each_row}" in out
    assert "{#tsv_with_names}" not in out


def test_fix_bare_urls_in_prose():
    en = "limited lifespan — no more than 12 hours (https://yandex.cloud/en/docs/x#lifetime)"
    out = fix_bare_urls_in_prose(en)
    assert "[lifespan — no more than 12 hours](https://yandex.cloud/en/docs/x#lifetime)" in out


def test_fix_broken_anchor_links():
    assert fix_broken_anchor_links("[#rag]()") == "[RAG](#rag)"


def test_fix_bare_relative_md_paths_cli_profile():
    ru = (
        "See [{{ ydb-short-name }} CLI documentation]"
        "(../../../reference/ydb-cli/profile/index.md).\n"
    )
    en = (
        "See {{ ydb-short-name }} CLI documentation "
        "(../../../reference/ydb-cli/profile/index.md).\n"
    )
    out = fix_bare_relative_md_paths_from_ru(ru, en)
    assert "](../../../reference/ydb-cli/profile/index.md)" in out


def test_restore_markdown_links_cli_profile_via_full_restore():
    ru = (
        "See [{{ ydb-short-name }} CLI documentation]"
        "(../../../reference/ydb-cli/profile/index.md).\n"
    )
    en = (
        "See {{ ydb-short-name }} CLI documentation "
        "(../../../reference/ydb-cli/profile/index.md).\n"
    )
    out = restore_markdown_links_from_ru(ru, en)
    assert "](../../../reference/ydb-cli/profile/index.md)" in out
    assert len(extract_relative_link_refs(ru)) == len(extract_relative_link_refs(out))


def test_restore_markdown_links_bare_relative_path():
    ru = "See [topology](../deployment-preparation.md#topology-select)."
    en = "See topology in ../deployment-preparation.md#topology-select."
    out = restore_markdown_links_from_ru(ru, en)
    assert "](../deployment-preparation.md#topology-select)" in out


def test_restore_markdown_links_iam_lifespan():
    ru = "IAM-токен имеет ограниченный [срок жизни — не более 12 часов](https://yandex.cloud/ru/docs/iam#lifetime)."
    en = "The IAM token has a limited lifespan — no more than 12 hours (https://yandex.cloud/en/docs/iam#lifetime)."
    out = restore_markdown_links_from_ru(ru, en)
    assert "[lifespan — no more than 12 hours](https://yandex.cloud/en/docs/iam#lifetime)" in out
