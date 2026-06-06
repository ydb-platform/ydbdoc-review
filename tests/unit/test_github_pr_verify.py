"""Tests for doc_verify pair loading (RU from source PR head)."""

from __future__ import annotations

from unittest.mock import MagicMock

from ydbdoc_review.github.pr import (
    load_verify_navigation_ru_texts,
    load_verify_pair_contents,
    source_pr_content_ref,
)
from ydbdoc_review.pipeline.pairs import DocPair, NavigationPair


def test_source_pr_content_ref_fork_head():
    gh = MagicMock()
    gh.get_pull.return_value = {
        "head": {
            "sha": "abc123",
            "repo": {
                "owner": {"login": "contributor"},
                "name": "ydb",
            },
        }
    }
    owner, repo, ref = source_pr_content_ref(gh, "ydb-platform", "ydb", 40070)
    assert owner == "contributor"
    assert repo == "ydb"
    assert ref == "abc123"


def test_load_verify_pair_contents_ru_from_api(tmp_path):
    repo = tmp_path / "repo"
    en_dir = repo / "ydb" / "docs" / "en"
    en_dir.mkdir(parents=True)
    (en_dir / "a.md").write_text("EN body\n", encoding="utf-8")

    gh = MagicMock()
    gh.get_pull.return_value = {
        "head": {
            "sha": "src-sha",
            "repo": {"owner": {"login": "o"}, "name": "r"},
        }
    }
    gh.get_file_text.return_value = "RU body\n"

    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        en_changed=False,
    )
    contents = load_verify_pair_contents(
        str(repo),
        [pair],
        merge_base_with="HEAD",
        gh=gh,
        owner="o",
        repo="r",
        source_pr=3,
    )
    assert len(contents) == 1
    assert contents[0].ru_text == "RU body\n"
    assert contents[0].en_text == "EN body\n"
    gh.get_file_text.assert_called_once_with(
        "o", "r", "ydb/docs/ru/a.md", "src-sha"
    )


def test_load_verify_navigation_ru_texts():
    gh = MagicMock()
    gh.get_pull.return_value = {
        "head": {
            "sha": "src-sha",
            "repo": {"owner": {"login": "o"}, "name": "r"},
        }
    }
    gh.get_file_text.return_value = "items:\n"
    pair = NavigationPair(
        ru_path="ydb/docs/ru/a/toc_i.yaml",
        en_path="ydb/docs/en/a/toc_i.yaml",
        en_changed=True,
    )
    texts = load_verify_navigation_ru_texts(
        [pair],
        gh=gh,
        owner="o",
        repo="r",
        source_pr=42414,
    )
    assert texts["ydb/docs/ru/a/toc_i.yaml"] == "items:\n"
