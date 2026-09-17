"""F-099: a changed publication target must fail without overwriting it."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.unit.test_publication_policy import (
    _pair_result,
    _run_top_level,
)
from tests.unit.test_publication_policy import (
    publication_repo as _publication_repo_fixture,
)
from ydbdoc_review.github.git_ops import RemoteRefMutationError
from ydbdoc_review.github.workflow import job_requires_nonzero_exit

publication_repo = _publication_repo_fixture


def _git(path: Path | str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _race_target_branch(
    tmp_path: Path,
    publication_repo: str,
) -> tuple[Path, str, Callable[..., list[str]]]:
    upstream = tmp_path / "upstream.git"
    subprocess.run(
        ["git", "clone", "--bare", publication_repo, str(upstream)],
        check=True,
        capture_output=True,
        text=True,
    )
    branch = "refs/heads/ydbdoc-review/pr-7"
    baseline_sha = _git(publication_repo, "rev-parse", "HEAD")
    _git(upstream, "update-ref", branch, baseline_sha)

    concurrent = tmp_path / "concurrent"
    subprocess.run(
        ["git", "clone", str(upstream), str(concurrent)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(concurrent, "config", "user.email", "other@example.com")
    _git(concurrent, "config", "user.name", "other")
    _git(concurrent, "checkout", "-b", "concurrent", f"origin/{branch.removeprefix('refs/heads/')}")
    (concurrent / "foreign.md").write_text("foreign change\n", encoding="utf-8")
    _git(concurrent, "add", "foreign.md")
    _git(concurrent, "commit", "-m", "concurrent target update")
    pushed = False

    def publish_concurrent_change(*_args: object, **_kwargs: object) -> list[str]:
        nonlocal pushed
        if not pushed:
            _git(concurrent, "push", "origin", f"HEAD:{branch}")
            pushed = True
        return []

    return upstream, baseline_sha, publish_concurrent_change


def test_F099_target_changed(tmp_path: Path, publication_repo: str) -> None:
    upstream, baseline_sha, race = _race_target_branch(tmp_path, publication_repo)

    job, gh, _prepare, _commit, push, _finish = _run_top_level(
        publication_repo,
        _pair_result(),
        remote_branch_exists=True,
        remote_branch_sha=baseline_sha,
        real_push_remote=str(upstream),
        real_git_commit=True,
        reconcile_side_effect=race,
    )

    candidate_sha = _git(publication_repo, "rev-parse", "HEAD")
    remote_sha = _git(upstream, "rev-parse", "refs/heads/ydbdoc-review/pr-7")
    assert remote_sha != baseline_sha
    assert remote_sha != candidate_sha
    assert (Path(publication_repo) / "ydb/docs/en/a.md").read_text(
        encoding="utf-8"
    ) == "Translated.\n"
    assert job.pr_result.publication_candidate_sha == candidate_sha
    assert job.pr_result.publication_failure == "target_write_conflict"
    assert job.pushed is False
    assert job_requires_nonzero_exit(job) is True
    assert push.call_count == 1
    gh.create_pull.assert_not_called()


def test_F099_error_evidence(tmp_path: Path, publication_repo: str) -> None:
    upstream, baseline_sha, race = _race_target_branch(tmp_path, publication_repo)
    result = _pair_result()
    file_result = result.pair_results[0].file_result
    assert file_result is not None
    file_result.input_tokens = 100
    file_result.output_tokens = 50
    file_result.estimated_cost_usd = 12.5

    job, gh, _prepare, _commit, _push, finish = _run_top_level(
        publication_repo,
        result,
        remote_branch_exists=True,
        remote_branch_sha=baseline_sha,
        real_push_remote=str(upstream),
        real_git_commit=True,
        reconcile_side_effect=race,
    )

    candidate_sha = _git(publication_repo, "rev-parse", "HEAD")
    source_comment = gh.post_issue_comment.call_args.args[3]
    assert "target_write_conflict" in source_comment
    assert candidate_sha in source_comment
    assert "~₽12.5" in source_comment
    assert "полный ручной перезапуск" in source_comment
    assert "F-131" in source_comment
    assert "автоматически" in source_comment
    assert job.pr_result.pair_results[0].target_text == "Translated.\n"
    assert finish.call_args.kwargs["status"] == "failed"
    assert finish.call_args.kwargs["cost_rub"] == 0.0
    assert gh.get_pull.call_count == 1
    assert gh.get_branch_sha.call_count == 1


def test_F099_red_conflict_does_not_open_pr_for_foreign_commit(
    tmp_path: Path,
    publication_repo: str,
) -> None:
    upstream, baseline_sha, race = _race_target_branch(tmp_path, publication_repo)
    result = _pair_result(target_text="See [missing](missing.md).\n")
    result.pair_results[0].source_text = "См. [missing](missing.md).\n"

    job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        result,
        remote_branch_exists=True,
        remote_branch_sha=baseline_sha,
        real_push_remote=str(upstream),
        real_git_commit=True,
        reconcile_side_effect=race,
    )

    assert job.pr_result.publication_failure == "target_write_conflict"
    gh.create_pull.assert_not_called()


def test_F099_transport_failure_is_not_reported_as_target_drift(
    tmp_path: Path,
    publication_repo: str,
) -> None:
    missing_remote = tmp_path / "missing.git"
    baseline_sha = _git(publication_repo, "rev-parse", "HEAD")

    with pytest.raises(RemoteRefMutationError, match="cannot preserve"):
        _run_top_level(
            publication_repo,
            _pair_result(),
            remote_branch_exists=True,
            remote_branch_sha=baseline_sha,
            real_push_remote=str(missing_remote),
            real_git_commit=True,
        )


def test_F099_concurrent_target_deletion_is_reported_as_conflict(
    tmp_path: Path,
    publication_repo: str,
) -> None:
    upstream = tmp_path / "deleted-upstream.git"
    subprocess.run(
        ["git", "clone", "--bare", publication_repo, str(upstream)],
        check=True,
        capture_output=True,
        text=True,
    )
    branch = "refs/heads/ydbdoc-review/pr-7"
    baseline_sha = _git(publication_repo, "rev-parse", "HEAD")
    _git(upstream, "update-ref", branch, baseline_sha)

    def delete_target(*_args: object, **_kwargs: object) -> list[str]:
        _git(upstream, "update-ref", "-d", branch)
        return []

    job, gh, _prepare, _commit, _push, _finish = _run_top_level(
        publication_repo,
        _pair_result(),
        remote_branch_exists=True,
        remote_branch_sha=baseline_sha,
        real_push_remote=str(upstream),
        real_git_commit=True,
        reconcile_side_effect=delete_target,
    )

    assert job.blocked is True
    assert job.pr_result.publication_failure == "target_write_conflict"
    assert gh.post_issue_comment.call_args.args[2] == 7
    assert "полный ручной перезапуск F-131" in gh.post_issue_comment.call_args.args[3]
    assert subprocess.run(
        ["git", "--git-dir", str(upstream), "rev-parse", branch],
        capture_output=True,
    ).returncode != 0
