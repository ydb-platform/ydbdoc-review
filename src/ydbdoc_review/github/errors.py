"""GitHub integration errors."""

from __future__ import annotations


class GitHubError(Exception):
    """Base class for GitHub API / git errors."""


class GitHubAPIError(GitHubError):
    """REST API request failed."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubConfigError(GitHubError):
    """Missing token or invalid configuration."""
