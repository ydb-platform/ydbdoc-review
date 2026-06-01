"""Tests for PR helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ydbdoc_review.github.pr import (
    build_pairs_from_changes,
    list_pr_file_changes_api,
    load_pair_contents,
    parse_repo,
    parse_source_pr_from_text,
    pull_request_context,
    source_pr_number_from_branch,
)


def test_parse_repo():
    assert parse_repo("ydb-platform/ydb") == ("ydb-platform", "ydb")


def test_parse_repo_invalid():
    with pytest.raises(ValueError):
        parse_repo("bad")


def test_source_pr_from_branch():
    assert source_pr_number_from_branch("ydbdoc-review/pr-42", prefix="ydbdoc-review/pr-") == 42
    assert source_pr_number_from_branch("feature/x", prefix="ydbdoc-review/pr-") is None


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
            }

    ctx = pull_request_context(FakeClient(), "o", "r", 5)  # type: ignore[arg-type]
    assert ctx.number == 5
    assert ctx.head_ref == "feat"
