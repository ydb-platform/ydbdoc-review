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
    prepare_translation_branch_on_base,
    read_text,
    read_text_at_ref,
    remote_push_url,
    rollback_pushed_branch,
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


def test_push_branch_force_remains_unguarded_for_non_red_callers(monkeypatch):
    """Legacy non-RED callers retain the explicit unguarded force option."""
    from ydbdoc_review.github import git_ops

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))

        class _Proc:
            returncode = 0
            stderr = ""
            stdout = ""

        return _Proc()

    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)
    monkeypatch.setattr(git_ops, "ensure_remote", lambda *a, **k: None)
    git_ops.push_branch(
        "/tmp/repo",
        "ydbdoc-review-push",
        "ydbdoc-review/pr-46798",
        "tok",
        "https://github.com/ydb-platform/ydb.git",
        force=True,
    )
    assert calls and "--force" in calls[0]
    assert "--force-with-lease" not in calls[0]
    assert "HEAD:refs/heads/ydbdoc-review/pr-46798" in calls[0]


def test_rollback_pushed_branch_refuses_to_clobber_concurrent_remote_update(
    git_repo: str,
    tmp_path: Path,
    monkeypatch,
):
    from ydbdoc_review.github import git_ops

    upstream = tmp_path / "upstream.git"
    subprocess.run(
        ["git", "clone", "--bare", git_repo, str(upstream)],
        check=True,
        capture_output=True,
    )
    previous_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    write_text(git_repo, "candidate.md", "candidate\n")
    git_commit_paths(git_repo, ["candidate.md"], "candidate", "test", "t@example.com")
    candidate_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch_ref = "refs/heads/ydbdoc-review/pr-7"
    subprocess.run(
        ["git", "push", str(upstream), f"HEAD:{branch_ref}"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    write_text(git_repo, "concurrent.md", "concurrent\n")
    git_commit_paths(
        git_repo,
        ["concurrent.md"],
        "concurrent",
        "test",
        "t@example.com",
    )
    concurrent_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "push", "--force", str(upstream), f"HEAD:{branch_ref}"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(git_ops, "remote_push_url", lambda *_args: str(upstream))

    with pytest.raises(RuntimeError, match="guarded lease failed"):
        rollback_pushed_branch(
            git_repo,
            "rollback-test",
            "ydbdoc-review/pr-7",
            "token",
            "https://github.com/o/r.git",
            expected_pushed_sha=candidate_sha,
            previous_sha=previous_sha,
        )

    remote_sha = subprocess.run(
        ["git", "--git-dir", str(upstream), "rev-parse", branch_ref],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remote_sha == concurrent_sha


def test_guarded_force_push_refuses_stale_remote_snapshot(
    git_repo: str,
    tmp_path: Path,
    monkeypatch,
):
    from ydbdoc_review.github import git_ops

    upstream = tmp_path / "guarded-upstream.git"
    subprocess.run(
        ["git", "clone", "--bare", git_repo, str(upstream)],
        check=True,
        capture_output=True,
    )
    branch_ref = "refs/heads/ydbdoc-review/pr-7"
    snapshot_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "push", str(upstream), f"HEAD:{branch_ref}"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    writer = tmp_path / "writer"
    subprocess.run(
        ["git", "clone", str(upstream), str(writer)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "writer@example.com"],
        cwd=writer,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "writer"],
        cwd=writer,
        check=True,
    )
    write_text(str(writer), "concurrent.md", "concurrent\n")
    git_commit_paths(
        str(writer),
        ["concurrent.md"],
        "concurrent",
        "writer",
        "writer@example.com",
    )
    concurrent_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=writer,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "push", "--force", str(upstream), f"HEAD:{branch_ref}"],
        cwd=writer,
        check=True,
        capture_output=True,
    )
    write_text(git_repo, "candidate.md", "candidate\n")
    git_commit_paths(git_repo, ["candidate.md"], "candidate", "test", "t@example.com")
    monkeypatch.setattr(git_ops, "remote_push_url", lambda *_args: str(upstream))

    with pytest.raises(RuntimeError, match="lease"):
        git_ops.push_branch(
            git_repo,
            "guarded-push",
            "ydbdoc-review/pr-7",
            "token",
            "https://github.com/o/r.git",
            force=True,
            guard_remote_ref=True,
            expected_remote_sha=snapshot_sha,
        )

    remote_sha = subprocess.run(
        ["git", "--git-dir", str(upstream), "rev-parse", branch_ref],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remote_sha == concurrent_sha


def test_guarded_force_push_expected_absence_refuses_concurrent_branch_creation(
    git_repo: str,
    tmp_path: Path,
    monkeypatch,
):
    from ydbdoc_review.github import git_ops

    upstream = tmp_path / "absent-guard-upstream.git"
    subprocess.run(
        ["git", "clone", "--bare", git_repo, str(upstream)],
        check=True,
        capture_output=True,
    )
    writer = tmp_path / "absence-writer"
    subprocess.run(
        ["git", "clone", str(upstream), str(writer)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "writer@example.com"],
        cwd=writer,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "writer"],
        cwd=writer,
        check=True,
    )
    write_text(str(writer), "concurrent.md", "concurrent\n")
    git_commit_paths(
        str(writer),
        ["concurrent.md"],
        "concurrent",
        "writer",
        "writer@example.com",
    )
    concurrent_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=writer,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch_ref = "refs/heads/ydbdoc-review/pr-7"
    subprocess.run(
        ["git", "push", str(upstream), f"HEAD:{branch_ref}"],
        cwd=writer,
        check=True,
        capture_output=True,
    )
    write_text(git_repo, "candidate.md", "candidate\n")
    git_commit_paths(git_repo, ["candidate.md"], "candidate", "test", "t@example.com")
    monkeypatch.setattr(git_ops, "remote_push_url", lambda *_args: str(upstream))

    with pytest.raises(RuntimeError, match="stale info"):
        git_ops.push_branch(
            git_repo,
            "guarded-push",
            "ydbdoc-review/pr-7",
            "token",
            "https://github.com/o/r.git",
            force=True,
            guard_remote_ref=True,
            expected_remote_sha=None,
        )

    remote_sha = subprocess.run(
        ["git", "--git-dir", str(upstream), "rev-parse", branch_ref],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remote_sha == concurrent_sha


def test_git_commit_paths_delete_ignore_unmatch(git_repo: str):
    """Regression: PR #37955 — EN mirror already absent on base must not fail."""
    ok = git_commit_paths(
        git_repo,
        [],
        "remove stale en",
        "test",
        "t@example.com",
        deleted_paths=["ydb/docs/en/core/dev/streaming-query/S3-enrichment.md"],
    )
    assert ok is False


def test_git_commit_paths_delete_existing_file(git_repo: str):
    write_text(git_repo, "ydb/docs/en/old-page.md", "# Old\n")
    subprocess.run(
        ["git", "-C", git_repo, "add", "ydb/docs/en/old-page.md"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", git_repo, "commit", "-m", "add old en"],
        check=True,
    )
    ok = git_commit_paths(
        git_repo,
        [],
        "remove old en",
        "test",
        "t@example.com",
        deleted_paths=["ydb/docs/en/old-page.md"],
    )
    assert ok is True
    assert read_text(git_repo, "ydb/docs/en/old-page.md") is None


def test_prepare_translation_branch_removes_deleted_on_base(tmp_path: Path):
    upstream = tmp_path / "upstream.git"
    upstream.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "main"], cwd=upstream, check=True)

    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "clone", str(upstream), str(work)], check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=work,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=work,
        check=True,
    )
    en = work / "ydb" / "docs" / "en"
    en.mkdir(parents=True)
    (en / "stale.md").write_text("# stale\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-m", "seed main"], cwd=work, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=work, check=True)

    write_text(str(work), "ydb/docs/en/new.md", "# New\n")
    prepare_translation_branch_on_base(
        str(work),
        translation_branch="ydbdoc-review/pr-37955",
        base_remote_url=str(upstream),
        base_remote_name="ydbdoc-review-upstream",
        base_branch="main",
        paths=["ydb/docs/en/new.md"],
        deleted_paths=["ydb/docs/en/stale.md"],
    )
    assert read_text(str(work), "ydb/docs/en/new.md") == "# New\n"
    assert read_text(str(work), "ydb/docs/en/stale.md") is None


def test_file_diff_range_after_edit(git_repo: str):
    write_text(git_repo, "ydb/docs/ru/a.md", "# Hi v2\n")
    subprocess.run(["git", "-C", git_repo, "add", "ydb/docs/ru/a.md"], check=True)
    subprocess.run(
        ["git", "-C", git_repo, "commit", "-m", "edit ru"],
        check=True,
    )
    diff = file_diff_range(git_repo, "HEAD~1", "ydb/docs/ru/a.md")
    assert "v2" in diff
