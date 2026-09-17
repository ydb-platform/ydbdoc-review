"""Tests for local git helpers."""

from __future__ import annotations

import subprocess
from inspect import getsource
from pathlib import Path

import pytest

import ydbdoc_review.github.git_ops as git_ops
from ydbdoc_review.github.git_ops import (
    _parse_name_status_z,
    file_diff_range,
    first_parent_commit_changes,
    git_commit_paths,
    list_local_changes,
    merge_base,
    prepare_translation_branch_on_base,
    read_text,
    read_text_at_ref,
    remote_push_url,
    resolve_commit_ref,
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


def test_first_parent_commit_changes_preserves_control_and_unicode_path(git_repo: str):
    path = "ydb/docs/ru/control\r\n\t`-\u0451-\u5b89\u5168.md"
    target = Path(git_repo) / path
    target.write_text("# Exact path\n", encoding="utf-8")
    subprocess.run(["git", "-C", git_repo, "add", "--", path], check=True)
    subprocess.run(["git", "-C", git_repo, "commit", "-m", "hostile path"], check=True)
    commit_sha = resolve_commit_ref(git_repo, "HEAD")

    assert first_parent_commit_changes(git_repo, commit_sha) == ((path, "added"),)


def test_first_parent_commit_changes_uses_new_path_for_scored_rename(git_repo: str):
    old_path = "ydb/docs/ru/old.md"
    new_path = "ydb/docs/ru/new.md"
    old_target = Path(git_repo) / old_path
    old_target.write_text("# Same bytes\n", encoding="utf-8")
    subprocess.run(["git", "-C", git_repo, "add", "--", old_path], check=True)
    subprocess.run(["git", "-C", git_repo, "commit", "-m", "old path"], check=True)
    subprocess.run(["git", "-C", git_repo, "mv", "--", old_path, new_path], check=True)
    subprocess.run(["git", "-C", git_repo, "commit", "-m", "rename path"], check=True)
    commit_sha = resolve_commit_ref(git_repo, "HEAD")

    assert first_parent_commit_changes(git_repo, commit_sha) == ((new_path, "modified"),)


@pytest.mark.parametrize("status", [b"R100", b"C100"])
def test_parse_name_status_z_preserves_scored_destination_bytes(status: bytes):
    """Scored rename/copy records retain only their exact destination path."""
    source = b"ydb/docs/ru/source\r\n\t-\xd1\x91-\xe5\xae\x89\xe5\x85\xa8.md"
    destination = b"ydb/docs/ru/destination\r\n\t-\xd1\x91-\xe5\xae\x89\xe5\x85\xa8.md"

    assert _parse_name_status_z(status + b"\0" + source + b"\0" + destination + b"\0") == (
        (destination.decode("utf-8"), "modified"),
    )


def test_first_parent_commit_changes_accepts_empty_diff(git_repo: str):
    subprocess.run(
        ["git", "-C", git_repo, "commit", "--allow-empty", "-m", "empty"],
        check=True,
    )
    commit_sha = resolve_commit_ref(git_repo, "HEAD")

    assert first_parent_commit_changes(git_repo, commit_sha) == ()


def test_parse_name_status_z_rejects_non_utf8_path_without_replacement():
    with pytest.raises(RuntimeError, match="UTF-8"):
        _parse_name_status_z(b"A\0ydb/docs/ru/bad-\xff.md\0")


def test_name_status_raw_bytes_contract_kills_text_and_splitline_mutants(git_repo: str):
    """The byte/NUL protocol must not be normalized through text lines."""
    path = "ydb/docs/ru/control\r\n\t-\u0451-\u5b89\u5168.md"
    Path(git_repo, path).write_text("# Exact path\n", encoding="utf-8")
    subprocess.run(["git", "-C", git_repo, "add", "--", path], check=True)
    subprocess.run(["git", "-C", git_repo, "commit", "-m", "raw bytes"], check=True)
    commit_sha = resolve_commit_ref(git_repo, "HEAD")
    assert first_parent_commit_changes(git_repo, commit_sha) == ((path, "added"),)

    first_parent_source = getsource(git_ops.first_parent_commit_changes).replace(
        "text=False", "text=True"
    )
    first_parent_globals = dict(vars(git_ops))
    exec(first_parent_source, first_parent_globals)
    with pytest.raises(TypeError):
        first_parent_globals["first_parent_commit_changes"](git_repo, commit_sha)

    parser_source = getsource(git_ops._parse_name_status_z).replace(
        'raw[:-1].split(b"\\0")', "raw[:-1].splitlines()"
    )
    parser_globals = dict(vars(git_ops))
    exec(parser_source, parser_globals)
    with pytest.raises(RuntimeError, match="name-status"):
        parser_globals["_parse_name_status_z"](
            b"A\0ydb/docs/ru/control\r\n\t-\xd1\x91-\xe5\xae\x89\xe5\x85\xa8.md\0"
        )


@pytest.mark.parametrize(
    "payload",
    [
        b"M\0path-without-terminal-nul",
        b"M\0\0",
        b"R100\0old.md\0",
        b"Q\0path.md\0",
        b"M\0path.md\0orphan\0",
    ],
)
def test_parse_name_status_z_rejects_truncated_or_invalid_records(payload: bytes):
    with pytest.raises(RuntimeError, match="name-status"):
        _parse_name_status_z(payload)


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


def test_git_commit_paths_ignores_untracked_state_when_selected_path_is_unchanged(
    git_repo: str,
):
    state_path = Path(git_repo) / ".ydbdoc-state" / "pr-7.json"
    state_path.parent.mkdir()
    state_bytes = b'{"continuable": false}\n'
    state_path.write_bytes(state_bytes)
    previous_head = resolve_commit_ref(git_repo, "HEAD")

    ok = git_commit_paths(
        git_repo,
        ["ydb/docs/ru/a.md"],
        "unchanged docs",
        "test",
        "t@example.com",
    )

    assert ok is False
    assert resolve_commit_ref(git_repo, "HEAD") == previous_head
    assert state_path.read_bytes() == state_bytes
    assert (
        subprocess.run(
            ["git", "-C", git_repo, "status", "--porcelain", "--", ".ydbdoc-state"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == "?? .ydbdoc-state/\n"
    )


def test_git_commit_paths_ignores_unstaged_tracked_change_when_selected_path_is_unchanged(
    git_repo: str,
):
    unrelated_path = Path(git_repo) / "unrelated.txt"
    unrelated_path.write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", git_repo, "add", "--", "unrelated.txt"], check=True)
    subprocess.run(
        ["git", "-C", git_repo, "commit", "-m", "add unrelated"],
        check=True,
    )
    unrelated_path.write_text("local edit\n", encoding="utf-8")
    previous_head = resolve_commit_ref(git_repo, "HEAD")

    ok = git_commit_paths(
        git_repo,
        ["ydb/docs/ru/a.md"],
        "unchanged docs",
        "test",
        "t@example.com",
    )

    assert ok is False
    assert resolve_commit_ref(git_repo, "HEAD") == previous_head
    assert unrelated_path.read_text(encoding="utf-8") == "local edit\n"
    assert (
        subprocess.run(
            ["git", "-C", git_repo, "diff", "--cached", "--name-only"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )
    assert (
        subprocess.run(
            ["git", "-C", git_repo, "status", "--porcelain", "--", "unrelated.txt"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == " M unrelated.txt\n"
    )


def test_git_commit_paths_commits_selected_change_without_staging_untracked_state(
    git_repo: str,
):
    selected_path = "ydb/docs/ru/a.md"
    write_text(git_repo, selected_path, "# Updated\n")
    state_path = Path(git_repo) / ".ydbdoc-state" / "pr-7.json"
    state_path.parent.mkdir()
    state_bytes = b'{"continuable": false}\n'
    state_path.write_bytes(state_bytes)

    ok = git_commit_paths(
        git_repo,
        [selected_path],
        "update docs",
        "test",
        "t@example.com",
    )

    assert ok is True
    assert first_parent_commit_changes(git_repo, "HEAD") == ((selected_path, "modified"),)
    assert state_path.read_bytes() == state_bytes
    assert (
        subprocess.run(
            ["git", "-C", git_repo, "status", "--porcelain", "--", ".ydbdoc-state"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == "?? .ydbdoc-state/\n"
    )


def test_git_commit_paths_propagates_staged_diff_probe_failure(
    git_repo: str,
    monkeypatch,
):
    real_run = git_ops.subprocess.run

    def fail_staged_diff_probe(cmd, *args, **kwargs):
        if cmd == ["git", "-C", git_repo, "diff", "--cached", "--quiet"]:
            return subprocess.CompletedProcess(
                cmd,
                2,
                stdout="",
                stderr="fatal: staged diff probe failed",
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(git_ops.subprocess, "run", fail_staged_diff_probe)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        git_commit_paths(
            git_repo,
            ["ydb/docs/ru/a.md"],
            "unchanged docs",
            "test",
            "t@example.com",
        )

    assert exc_info.value.returncode == 2


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


def test_guarded_force_push_rejects_update_after_preservation_fetch(
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
    write_text(git_repo, "candidate.md", "candidate\n")
    git_commit_paths(git_repo, ["candidate.md"], "candidate", "test", "t@example.com")
    monkeypatch.setattr(git_ops, "remote_push_url", lambda *_args: str(upstream))
    real_run = git_ops.subprocess.run
    injected = False

    def _inject_after_fetch(cmd, *args, **kwargs):
        nonlocal injected
        if (
            not injected
            and isinstance(cmd, list)
            and len(cmd) >= 5
            and cmd[:4] == ["git", "-C", git_repo, "push"]
            and "guarded-push" in cmd
        ):
            injected = True
            real_run(
                ["git", "push", "--force", str(upstream), f"HEAD:{branch_ref}"],
                cwd=writer,
                check=True,
                capture_output=True,
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(git_ops.subprocess, "run", _inject_after_fetch)

    with pytest.raises(RuntimeError, match="stale info"):
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

    assert injected is True
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
    base_commit_sha = resolve_commit_ref(str(work), "HEAD")

    write_text(str(work), "ydb/docs/en/new.md", "# New\n")
    prepare_translation_branch_on_base(
        str(work),
        translation_branch="ydbdoc-review/pr-37955",
        base_remote_url=str(upstream),
        base_remote_name="ydbdoc-review-upstream",
        base_branch="main",
        paths=["ydb/docs/en/new.md"],
        base_commit_sha=base_commit_sha,
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
