"""Tests for selecting the authoritative source-PR translation scope."""

from ydbdoc_review.github.pr import PullRequestContext, source_pr_scope_changes


def _context(*, merged: bool) -> PullRequestContext:
    return PullRequestContext(
        owner="ydb-platform",
        repo="ydb",
        number=40385,
        title="docs",
        head_ref="docs/source",
        head_sha="head",
        head_repo_full_name="ydb-platform/ydb",
        head_repo_https_url="https://github.com/ydb-platform/ydb.git",
        base_ref="main",
        merged=merged,
        merge_commit_sha="merge" if merged else None,
    )


def test_merged_pr_scope_ignores_repository_drift_from_git_diff():
    git_changes = [
        ("ydb/docs/en/core/reference/configuration/monitoring_config.md", "modified"),
        ("ydb/docs/en/core/security/toc_p.yaml", "modified"),
    ]
    api_changes = [
        ("ydb/docs/ru/core/reference/configuration/monitoring_config.md", "modified"),
        ("ydb/docs/ru/core/security/toc_p.yaml", "modified"),
    ]

    assert source_pr_scope_changes(_context(merged=True), git_changes, api_changes) == api_changes


def test_open_pr_scope_keeps_union_of_checkout_and_api_changes():
    git_changes = [("ydb/docs/ru/a.md", "modified")]
    api_changes = [("ydb/docs/ru/b.md", "added")]

    assert source_pr_scope_changes(_context(merged=False), git_changes, api_changes) == [
        ("ydb/docs/ru/a.md", "modified"),
        ("ydb/docs/ru/b.md", "added"),
    ]
