"""F-091 publication-base contract tests."""

from __future__ import annotations

from ydbdoc_review.github import pr
from ydbdoc_review.github.pr import PullRequestContext
from ydbdoc_review.reporting.builder import build_translation_pr_body


def _ctx(
    *,
    head_repo: str = "ydb-platform/ydb",
    merged: bool = False,
    state: str = "open",
) -> PullRequestContext:
    return PullRequestContext(
        owner="ydb-platform",
        repo="ydb",
        number=91,
        title="source",
        head_ref="feature/docs",
        head_sha="h" * 40,
        head_repo_full_name=head_repo,
        head_repo_https_url=f"https://github.com/{head_repo}.git",
        base_ref="main",
        base_sha="b" * 40,
        merged=merged,
        state=state,
        merge_commit_sha="m" * 40 if merged else None,
    )


def test_F091_pr_modes() -> None:
    same = pr.publication_plan(_ctx())
    fork = pr.publication_plan(_ctx(head_repo="contributor/ydb"))
    merged = pr.publication_plan(_ctx(merged=True, state="closed"))
    closed = pr.publication_plan(_ctx(state="closed"))

    assert (same.branch_base_ref, same.pr_base_ref, same.reason) == (
        "feature/docs",
        "feature/docs",
        "open same-repository source PR",
    )
    assert (fork.branch_base_ref, fork.pr_base_ref, fork.reason) == (
        "main",
        "main",
        "open fork source PR",
    )
    assert (merged.branch_base_ref, merged.pr_base_ref, merged.reason) == (
        "main",
        "main",
        "merged source PR",
    )
    assert closed.publish is False
    assert "closed" in closed.reason


def test_F091_advanced_base() -> None:
    merged = pr.publication_plan(_ctx(merged=True, state="closed"))
    assert merged.source_head_sha == "m" * 40
    assert merged.source_base_sha == "b" * 40
    assert merged.publication_base_ref == "main"

    body = build_translation_pr_body(
        91,
        "ydb-platform/ydb",
        publication_plan=merged,
    )
    assert "head=`" + "m" * 12 in body
    assert "base=`" + "main" in body
    assert "merged source PR" in body
