"""F-095 contract tests for repository-CI independence."""

from __future__ import annotations

import inspect

import pytest

from ydbdoc_review.github import workflow
from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.github.pr import PullRequestContext, publication_plan


@pytest.mark.parametrize("ci_state", ["pending", "failed", "unavailable"])
def test_F095_ci_states(ci_state: str) -> None:
    """Repository CI state is not an input to translation publication."""
    context = PullRequestContext(
        owner="owner",
        repo="docs",
        number=95,
        title="Source PR",
        head_ref="source-pr-95",
        head_sha="head-sha",
        head_repo_full_name="owner/docs",
        head_repo_https_url="https://github.com/owner/docs.git",
        base_ref="main",
        base_sha="base-sha",
        body=f"repository-ci: {ci_state}",
    )

    plan = publication_plan(context)

    assert plan.publish is True
    assert plan.pr_base_ref == "source-pr-95"
    assert plan.publication_base_ref == "source-pr-95"


def test_F095_no_ci_actions() -> None:
    """doc_translate has no repository-CI polling, rerun, or dispatch API."""
    source = "\n".join(
        (
            inspect.getsource(workflow.run_doc_translate),
            inspect.getsource(GitHubClient),
        )
    )
    forbidden_actions = (
        "get_check_runs",
        "get_workflow_runs",
        "rerun_workflow",
        "create_workflow_dispatch",
        "check_suite",
        "workflow_dispatch",
        "actions/runs",
        "actions/check-runs",
    )

    assert all(action not in source for action in forbidden_actions)
