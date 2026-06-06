"""GitHub integration: API client, git ops, PR workflow."""

from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.github.errors import GitHubAPIError, GitHubConfigError, GitHubError
from ydbdoc_review.github.pr import (
    PullRequestContext,
    build_pairs_from_changes,
    list_pr_file_changes_api,
    list_pr_file_changes_git,
    load_pair_contents,
    parse_repo,
    pull_request_context,
)
__all__ = [
    "DocJobResult",
    "GitHubAPIError",
    "GitHubClient",
    "GitHubConfigError",
    "GitHubError",
    "PullRequestContext",
    "build_pairs_from_changes",
    "list_pr_file_changes_api",
    "list_pr_file_changes_git",
    "load_pair_contents",
    "parse_repo",
    "pull_request_context",
    "run_doc_translate",
    "run_doc_verify",
]


def __getattr__(name: str):
    if name == "DocJobResult":
        from ydbdoc_review.github.workflow import DocJobResult

        return DocJobResult
    if name == "run_doc_translate":
        from ydbdoc_review.github.workflow import run_doc_translate

        return run_doc_translate
    if name == "run_doc_verify":
        from ydbdoc_review.github.workflow import run_doc_verify

        return run_doc_verify
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
