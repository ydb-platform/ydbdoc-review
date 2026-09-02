"""Tests for PR helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ydbdoc_review.github.pr import (
    PullRequestContext,
    build_pairs_from_changes,
    is_fork_head,
    is_translation_pr_branch,
    is_verify_fixup_branch,
    list_pr_file_changes_api,
    load_pair_contents,
    parse_repo,
    parse_source_pr_from_text,
    pull_request_context,
    repo_https_clone_url,
    source_pr_number_from_branch,
    translation_branch_base,
    translation_pr_base,
    verify_fixup_pr_base,
)


def test_parse_repo():
    assert parse_repo("ydb-platform/ydb") == ("ydb-platform", "ydb")


def test_repo_https_clone_url():
    assert repo_https_clone_url("ydb-platform", "ydb") == (
        "https://github.com/ydb-platform/ydb.git"
    )


def test_is_fork_head():
    same = PullRequestContext(
        number=1,
        title="t",
        owner="o",
        repo="r",
        head_ref="feat",
        head_sha="abc",
        head_repo_https_url="https://github.com/o/r.git",
        head_repo_full_name="o/r",
        base_ref="main",
    )
    fork = PullRequestContext(
        number=2,
        title="t",
        owner="o",
        repo="r",
        head_ref="feat",
        head_sha="abc",
        head_repo_https_url="https://github.com/contrib/r.git",
        head_repo_full_name="contrib/r",
        base_ref="main",
    )
    assert is_fork_head(same) is False
    assert is_fork_head(fork) is True


def test_translation_branch_base():
    same = PullRequestContext(
        number=1,
        title="t",
        owner="o",
        repo="r",
        head_ref="feature/docs",
        head_sha="abc",
        head_repo_https_url="https://github.com/o/r.git",
        head_repo_full_name="o/r",
        base_ref="main",
    )
    fork = PullRequestContext(
        number=2,
        title="t",
        owner="o",
        repo="r",
        head_ref="contrib-feature",
        head_sha="abc",
        head_repo_https_url="https://github.com/contrib/r.git",
        head_repo_full_name="contrib/r",
        base_ref="main",
    )
    assert translation_branch_base(same) == (
        "https://github.com/o/r.git",
        "feature/docs",
    )
    assert translation_branch_base(fork) == (
        "https://github.com/o/r.git",
        "main",
    )
    assert translation_pr_base(same) == "feature/docs"
    assert translation_pr_base(fork) == "main"


def test_translation_branch_base_merged_same_repo():
    """Merged PR: head branch may be deleted; base translation branch on main."""
    merged = PullRequestContext(
        number=40070,
        title="t",
        owner="ydb-platform",
        repo="ydb",
        head_ref="alexnick88-patch-1",
        head_sha="b2a17bd",
        head_repo_https_url="https://github.com/ydb-platform/ydb.git",
        head_repo_full_name="ydb-platform/ydb",
        base_ref="main",
        merged=True,
        state="closed",
    )
    assert translation_branch_base(merged) == (
        "https://github.com/ydb-platform/ydb.git",
        "main",
    )
    assert translation_pr_base(merged) == "main"


def test_verify_fixup_pr_base():
    translation = PullRequestContext(
        number=11,
        title="t",
        owner="o",
        repo="r",
        head_ref="ydbdoc-review/pr-3",
        head_sha="abc",
        head_repo_https_url="https://github.com/o/r.git",
        head_repo_full_name="o/r",
        base_ref="feature/docs",
    )
    author = PullRequestContext(
        number=7,
        title="t",
        owner="o",
        repo="r",
        head_ref="feature/docs",
        head_sha="abc",
        head_repo_https_url="https://github.com/o/r.git",
        head_repo_full_name="o/r",
        base_ref="main",
    )
    prefix = "ydbdoc-review/pr-"
    assert verify_fixup_pr_base(translation, translation_branch_prefix=prefix) == (
        "ydbdoc-review/pr-3"
    )
    assert verify_fixup_pr_base(author, translation_branch_prefix=prefix) == "main"


def test_parse_repo_invalid():
    with pytest.raises(ValueError):
        parse_repo("bad")


def test_source_pr_from_branch():
    assert source_pr_number_from_branch("ydbdoc-review/pr-42", prefix="ydbdoc-review/pr-") == 42
    assert source_pr_number_from_branch("feature/x", prefix="ydbdoc-review/pr-") is None
    assert is_translation_pr_branch("ydbdoc-review/pr-42", translation_branch_prefix="ydbdoc-review/pr-")
    assert not is_translation_pr_branch("ydbdoc-review/verify-42", translation_branch_prefix="ydbdoc-review/pr-")
    assert is_verify_fixup_branch(
        "ydbdoc-review/verify-42", verify_fixup_branch_prefix="ydbdoc-review/verify-"
    )
    assert not is_verify_fixup_branch(
        "ydbdoc-review/pr-42", verify_fixup_branch_prefix="ydbdoc-review/verify-"
    )
    assert source_pr_number_from_branch(
        "ydbdoc-review/verify-46742", prefix="ydbdoc-review/verify-"
    ) == 46742


def test_parse_source_pr_from_text():
    assert parse_source_pr_from_text("Auto-translate docs from PR #17") == 17
    assert parse_source_pr_from_text("Branch ydbdoc-review/pr-9") == 9


def test_build_pairs_from_changes():
    changes = [
        ("ydb/docs/ru/foo.md", "modified"),
        ("ydb/docs/en/bar.md", "added"),
    ]
    pairs = build_pairs_from_changes(changes, docs_root="ydb/docs")
    assert len(pairs) == 2
    paths = {(p.ru_path, p.en_path) for p in pairs}
    assert ("ydb/docs/ru/foo.md", "ydb/docs/en/foo.md") in paths


def test_list_pr_file_changes_api():
    class FakeClient:
        def iter_pull_files(self, owner, repo, pr_number):
            yield {"filename": "ydb/docs/ru/x.md", "status": "added"}
            yield {"filename": "ydb/docs/en/y.md", "status": "removed"}

    changes = list_pr_file_changes_api(FakeClient(), "o", "r", 1)  # type: ignore[arg-type]
    assert ("ydb/docs/ru/x.md", "added") in changes
    assert ("ydb/docs/en/y.md", "deleted") in changes


@pytest.fixture
def git_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    ru = repo / "ydb" / "docs" / "ru"
    ru.mkdir(parents=True)
    (ru / "a.md").write_text("# RU\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    return str(repo)


def test_load_pair_contents(git_repo: str):
    pairs = build_pairs_from_changes(
        [("ydb/docs/ru/a.md", "modified")],
        docs_root="ydb/docs",
    )
    contents = load_pair_contents(git_repo, pairs, merge_base_with="HEAD")
    assert len(contents) == 1
    assert contents[0].ru_text and "RU" in contents[0].ru_text


def test_load_pair_contents_merged_pr_uses_pre_merge_ru_base(git_repo: str):
    ru = Path(git_repo) / "ydb" / "docs" / "ru" / "a.md"
    base_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()
    ru.write_text("# RU\n\n\u041d\u043e\u0432\u044b\u0439 \u0440\u0430\u0437\u0434\u0435\u043b.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "merged source"], cwd=git_repo, check=True)
    merge_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()
    pairs = build_pairs_from_changes(
        [("ydb/docs/ru/a.md", "modified")], docs_root="ydb/docs"
    )

    content = load_pair_contents(
        git_repo,
        pairs,
        merge_base_with=merge_sha,
        ru_content_ref=merge_sha,
        ru_base_ref=base_sha,
    )[0]

    assert content.ru_text == "# RU\n\n\u041d\u043e\u0432\u044b\u0439 \u0440\u0430\u0437\u0434\u0435\u043b.\n"
    assert content.ru_base_text == "# RU\n"


def test_load_pair_contents_merged_pr_prefers_tip_en_over_stale_checkout(git_repo: str):
    """§6.228 / #40385: merged checkout EN is stale; tip EN wins."""
    en = Path(git_repo) / "ydb" / "docs" / "en"
    en.mkdir(parents=True)
    en_page = en / "a.md"
    en_page.write_text(
        "See [nodes](../concepts/node.md#vklyuchenie-rezhima).\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "stale merge en"], cwd=git_repo, check=True)
    merge_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()

    # Tip (main) fixed the EN href after the historical merge commit.
    tip_en = "See [nodes](../concepts/node.md#enabling-mode).\n"
    en_page.write_text(tip_en, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "tip en fixed"], cwd=git_repo, check=True)
    tip_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()

    # Simulate action checkout at the historical merge commit.
    subprocess.run(["git", "checkout", "-q", merge_sha], cwd=git_repo, check=True)
    assert "vklyuchenie" in en_page.read_text(encoding="utf-8")

    pairs = build_pairs_from_changes(
        [("ydb/docs/ru/a.md", "modified")], docs_root="ydb/docs"
    )
    content = load_pair_contents(
        git_repo,
        pairs,
        merge_base_with=tip_sha,
        ru_content_ref=merge_sha,
        ru_base_ref=f"{merge_sha}^",
    )[0]

    assert content.en_text == tip_en
    assert content.en_base_text == tip_en
    assert "vklyuchenie" not in (content.en_text or "")


def test_load_pair_contents_pinned_snapshot_has_no_checkout_or_head_fallback(git_repo: str):
    ru_path = "ydb/docs/ru/a.md"
    en_path = "ydb/docs/en/a.md"
    Path(git_repo, en_path).parent.mkdir(parents=True, exist_ok=True)
    Path(git_repo, en_path).write_text("source base EN\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "source base"], cwd=git_repo, check=True)
    source_base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()
    Path(git_repo, ru_path).write_text("translation RU\n", encoding="utf-8")
    Path(git_repo, en_path).write_text("translation EN\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "translation"], cwd=git_repo, check=True)
    translation = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()
    Path(git_repo, ru_path).write_text("publication RU\n", encoding="utf-8")
    Path(git_repo, en_path).write_text("publication EN\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "publication"], cwd=git_repo, check=True)
    publication = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()
    Path(git_repo, ru_path).write_text("HEAD RU\n", encoding="utf-8")
    Path(git_repo, en_path).write_text("HEAD EN\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "head"], cwd=git_repo, check=True)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, text=True
    ).strip()
    Path(git_repo, ru_path).write_text("worktree sentinel\n", encoding="utf-8")
    Path(git_repo, en_path).write_text("worktree sentinel\n", encoding="utf-8")

    assert len({translation, publication, head}) == 3

    pair = build_pairs_from_changes([(ru_path, "modified")], docs_root="ydb/docs")[0]
    content = load_pair_contents(
        git_repo,
        [pair],
        merge_base_with=publication,
        ru_content_ref=translation,
        ru_base_ref=source_base,
    )[0]

    assert content.ru_text == "translation RU\n"
    assert content.en_text == "publication EN\n"
    assert content.ru_base_text == "# RU\n"
    assert content.en_base_text == "publication EN\n"
    assert content.ru_diff_vs_base is None
    assert content.en_diff_vs_base is None
    missing_pair = build_pairs_from_changes(
        [("ydb/docs/ru/missing.md", "modified")], docs_root="ydb/docs"
    )[0]
    missing = load_pair_contents(
        git_repo,
        [missing_pair],
        merge_base_with=publication,
        ru_content_ref=translation,
        ru_base_ref=source_base,
    )[0]
    assert (missing.ru_text, missing.en_text, missing.ru_base_text, missing.en_base_text) == (
        None,
        None,
        None,
        None,
    )


def test_pull_request_context():
    class FakeClient:
        def get_pull(self, owner, repo, pr_number):
            return {
                "title": "t",
                "head": {
                    "ref": "feat",
                    "sha": "abc",
                    "repo": {
                        "clone_url": "https://github.com/o/r.git",
                        "full_name": "o/r",
                    },
                },
                "base": {"ref": "main"},
                "merged": False,
                "state": "open",
            }

    ctx = pull_request_context(FakeClient(), "o", "r", 5)  # type: ignore[arg-type]
    assert ctx.number == 5
    assert ctx.head_ref == "feat"
    assert ctx.merged is False
