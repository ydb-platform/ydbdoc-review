"""Tests for doc_verify pair loading (RU from source PR head)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ydbdoc_review.github.pr import (
    load_verify_navigation_ru_texts,
    load_verify_pair_contents,
    pick_verify_ru_text,
    source_pr_content_ref,
    source_pr_content_ref_from_pull,
    translate_ru_content_ref,
    PullRequestContext,
)
from ydbdoc_review.pipeline.pairs import DocPair, NavigationPair
from ydbdoc_review.validation.fence_integrity import check_fence_body_copy


def test_source_pr_content_ref_fork_head():
    gh = MagicMock()
    gh.get_pull.return_value = {
        "merged": False,
        "head": {
            "sha": "abc123",
            "repo": {
                "owner": {"login": "contributor"},
                "name": "ydb",
            },
        },
    }
    owner, repo, ref = source_pr_content_ref(gh, "ydb-platform", "ydb", 40070)
    assert owner == "contributor"
    assert repo == "ydb"
    assert ref == "abc123"


def test_source_pr_content_ref_merged_keeps_head():
    """§6.109: primary ref is PR head (doc_translate checkout), not merge commit."""
    gh = MagicMock()
    gh.get_pull.return_value = {
        "merged": True,
        "merge_commit_sha": "merge999",
        "head": {
            "sha": "pr-head",
            "repo": {"owner": {"login": "contributor"}, "name": "ydb"},
        },
    }
    owner, repo, ref = source_pr_content_ref(gh, "ydb-platform", "ydb", 44457)
    assert owner == "contributor"
    assert repo == "ydb"
    assert ref == "pr-head"


def test_translate_ru_content_ref_merged_uses_merge_commit():
    """§6.120: doc_translate RU from merge commit, not stale feature head."""
    ctx = PullRequestContext(
        owner="ydb-platform",
        repo="ydb",
        number=43010,
        title="spring",
        head_ref="feature",
        head_sha="stale-head",
        head_repo_full_name="ydb-platform/ydb",
        head_repo_https_url="https://github.com/ydb-platform/ydb.git",
        base_ref="main",
        merged=True,
        merge_commit_sha="merge-landed",
    )
    assert translate_ru_content_ref(ctx) == "merge-landed"


def test_translate_ru_content_ref_open_pr_uses_checkout():
    ctx = PullRequestContext(
        owner="ydb-platform",
        repo="ydb",
        number=1,
        title="open",
        head_ref="feature",
        head_sha="head",
        head_repo_full_name="ydb-platform/ydb",
        head_repo_https_url="https://github.com/ydb-platform/ydb.git",
        base_ref="main",
        merged=False,
        merge_commit_sha=None,
    )
    assert translate_ru_content_ref(ctx) is None


def test_source_pr_content_ref_from_pull_merged_without_merge_sha_falls_back_to_head():
    owner, repo, ref = source_pr_content_ref_from_pull(
        {
            "merged": True,
            "merge_commit_sha": "",
            "head": {
                "sha": "head-only",
                "repo": {"owner": {"login": "o"}, "name": "r"},
            },
        },
        "o",
        "r",
        1,
    )
    assert ref == "head-only"


def test_load_verify_pair_contents_ru_from_api(tmp_path):
    repo = tmp_path / "repo"
    en_dir = repo / "ydb" / "docs" / "en"
    en_dir.mkdir(parents=True)
    (en_dir / "a.md").write_text("EN body\n", encoding="utf-8")

    gh = MagicMock()
    gh.get_pull.return_value = {
        "merged": False,
        "head": {
            "sha": "src-sha",
            "repo": {"owner": {"login": "o"}, "name": "r"},
        },
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


def test_load_verify_pair_contents_merged_prefers_head_when_segments_match(tmp_path):
    """Regression #46674: merge commit grew vs translate-from-head EN."""
    repo = tmp_path / "repo"
    en_dir = repo / "ydb" / "docs" / "en"
    en_dir.mkdir(parents=True)
    en_body = "# Title\n\nPara EN.\n"
    (en_dir / "a.md").write_text(en_body, encoding="utf-8")

    ru_head = "# Title\n\nPara.\n"
    ru_merge = "# Title\n\nPara.\n\n### Extra\n\nMore.\n"

    gh = MagicMock()
    gh.get_pull.return_value = {
        "merged": True,
        "merge_commit_sha": "merge-sha",
        "head": {
            "sha": "head-sha",
            "repo": {"owner": {"login": "contributor"}, "name": "ydb"},
        },
    }

    def _get_file(owner, repo_name, path, ref):
        assert path == "ydb/docs/ru/a.md"
        if ref == "head-sha":
            return ru_head
        if ref == "merge-sha":
            return ru_merge
        raise AssertionError(f"unexpected ref {ref}")

    gh.get_file_text.side_effect = _get_file

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
        source_pr=44457,
    )
    assert contents[0].ru_text == ru_head
    assert gh.get_file_text.call_count == 2


def test_pick_verify_ru_text_prefers_head_over_longer_merge():
    ru_head = "# Title\n\nPara.\n"
    ru_merge = "# Title\n\nPara.\n\n### Extra\n\nMore.\n"
    en = "# Title\n\nPara EN.\n"
    assert (
        pick_verify_ru_text(
            en_text=en,
            ru_api=ru_head,
            ru_merge=ru_merge,
            ru_local=ru_merge + "\n### Local\n\nX.\n",
            source_pr_merged=True,
        )
        == ru_head
    )


def test_pick_verify_ru_text_prefers_api_when_counts_match():
    ru_api = "# Title\n\nPara.\n"
    ru_local = "# Title\n\nPara.\n\nExtra.\n"
    en = "# Title\n\nPara EN.\n"
    assert pick_verify_ru_text(en_text=en, ru_api=ru_api, ru_local=ru_local) == ru_api


def test_pick_verify_ru_text_prefers_local_when_fence_bodies_match_en():
    """Regression #43997/#46609: stale PR head RU vs main after squash merge."""
    fence = (
        "  ```rust\n"
        "  use ydb::TxMode;\n\n"
        "  client\n"
        "      .query_client()\n"
        "      .retry_tx(async |tx| {\n"
        "          tx.query_row(\"SELECT 1\").await?;\n"
        "          Ok(())\n"
        "      })\n"
        "      .await?;\n"
        "  ```\n"
    )
    ru_local = f"# Tx control\n\n{{% list tabs %}}\n\n- Rust\n\n{fence}"
    ru_api = (
        ru_local.replace("use ydb::TxMode;", "use ydb::{{QueryTransactionOptions, QueryTxMode}};")
        .replace(".retry_tx", ".retry_transaction")
    )
    en = ru_local.replace("# Tx control", "# Transaction control")
    assert pick_verify_ru_text(
        en_text=en,
        ru_api=ru_api,
        ru_local=ru_local,
        source_pr_merged=True,
    ) == ru_local
    assert check_fence_body_copy(ru_api, en, source_lang="ru")
    assert not check_fence_body_copy(ru_local, en, source_lang="ru")


def test_pick_verify_ru_text_merged_tie_breaks_to_local_when_fence_counts_equal():
    ru = "# Title\n\nPara.\n"
    en = "# Title\n\nPara EN.\n"
    assert (
        pick_verify_ru_text(
            en_text=en,
            ru_api=ru,
            ru_local=ru + "\n",
            source_pr_merged=True,
        )
        == ru + "\n"
    )


def test_pick_verify_ru_text_uses_local_when_api_mismatch(tmp_path):
    """Regression #44872: EN aligned to checkout RU after source PR merged."""
    ydb = "/Users/iuriisintiaev/projects/ydb"
    import subprocess
    try:
        en = subprocess.check_output(
            ["git", "-C", ydb, "show", "origin/ydbdoc-review/pr-38700:ydb/docs/en/core/concepts/backup.md"],
            text=True,
        )
        ru_api = subprocess.check_output(
            ["git", "-C", ydb, "show", "pr-38700:ydb/docs/ru/core/concepts/backup.md"],
            text=True,
        )
        ru_local = subprocess.check_output(
            ["git", "-C", ydb, "show", "origin/ydbdoc-review/pr-38700:ydb/docs/ru/core/concepts/backup.md"],
            text=True,
        )
    except Exception:
        pytest.skip("ydb checkout with PR branches not available")
    picked = pick_verify_ru_text(en_text=en, ru_api=ru_api, ru_local=ru_local)
    assert picked == ru_local


def test_load_verify_pair_contents_uses_local_when_api_segments_differ(tmp_path):
    repo = tmp_path / "repo"
    ru_dir = repo / "ydb" / "docs" / "ru"
    en_dir = repo / "ydb" / "docs" / "en"
    ru_dir.mkdir(parents=True)
    en_dir.mkdir(parents=True)
    ru_local = "# T\n\n" + "Para.\n\n" * 30
    en_body = "# T\n\n" + "Para EN.\n\n" * 30
    (ru_dir / "a.md").write_text(ru_local, encoding="utf-8")
    (en_dir / "a.md").write_text(en_body, encoding="utf-8")

    gh = MagicMock()
    gh.get_pull.return_value = {
        "merged": False,
        "head": {
            "sha": "src-sha",
            "repo": {"owner": {"login": "o"}, "name": "r"},
        },
    }
    gh.get_file_text.return_value = "# T\n\nPara.\n"

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
        source_pr=38700,
    )
    assert contents[0].ru_text == ru_local


def test_load_verify_navigation_ru_texts():
    gh = MagicMock()
    gh.get_pull.return_value = {
        "merged": False,
        "head": {
            "sha": "src-sha",
            "repo": {"owner": {"login": "o"}, "name": "r"},
        },
    }
    gh.get_file_text.return_value = "items:\n"
    pair = NavigationPair(
        ru_path="ydb/docs/ru/a/toc_i.yaml",
        en_path="ydb/docs/en/a/toc_i.yaml",
        en_changed=True,
    )
    texts = load_verify_navigation_ru_texts(
        [pair],
        repo_path="/tmp",
        gh=gh,
        owner="o",
        repo="r",
        source_pr=42414,
    )
    assert texts["ydb/docs/ru/a/toc_i.yaml"] == "items:\n"
