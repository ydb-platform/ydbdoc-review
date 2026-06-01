"""Tests for local git helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ydbdoc_review.github.git_ops import (
    file_diff_range,
    git_commit_paths,
    list_local_changes,
    merge_base,
    read_text,
    read_text_at_ref,
    remote_push_url,
    write_text,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=repo,
        check=True,
    )
    docs = repo / "ydb" / "docs" / "ru"
    docs.mkdir(parents=True)
    (docs / "a.md").write_text("# Hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    return str(repo)


def test_write_read(git_repo: str):
    write_text(git_repo, "ydb/docs/en/a.md", "Hello\n")
    assert read_text(git_repo, "ydb/docs/en/a.md") == "Hello\n"
    mb = merge_base(git_repo, "HEAD", "HEAD")
    assert mb


def test_list_local_changes_after_commit(git_repo: str):
    write_text(git_repo, "ydb/docs/en/a.md", "Hello\n")
    subprocess.run(["git", "-C", git_repo, "add", "ydb/docs/en/a.md"], check=True)
    subprocess.run(
        ["git", "-C", git_repo, "commit", "-m", "add en"],
        check=True,
    )
    changes = list_local_changes(git_repo, "HEAD~1")
    assert ("ydb/docs/en/a.md", "added") in changes


def test_read_text_at_ref(git_repo: str):
    text = read_text_at_ref(git_repo, "HEAD", "ydb/docs/ru/a.md")
    assert text and "Hi" in text


def test_git_commit_paths(git_repo: str):
    write_text(git_repo, "ydb/docs/en/a.md", "Hello\n")
    ok = git_commit_paths(
        git_repo,
        ["ydb/docs/en/a.md"],
        "add en",
        "test",
        "t@example.com",
    )
    assert ok is True


def test_remote_push_url():
    url = remote_push_url("https://github.com/o/r.git", "secret")
    assert "x-access-token:secret@github.com" in url


def test_file_diff_range_after_edit(git_repo: str):
    write_text(git_repo, "ydb/docs/ru/a.md", "# Hi v2\n")
    subprocess.run(["git", "-C", git_repo, "add", "ydb/docs/ru/a.md"], check=True)
    subprocess.run(
        ["git", "-C", git_repo, "commit", "-m", "edit ru"],
        check=True,
    )
    diff = file_diff_range(git_repo, "HEAD~1", "ydb/docs/ru/a.md")
    assert "v2" in diff
