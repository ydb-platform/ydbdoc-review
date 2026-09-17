"""Confirm older Actions workers have stopped before replacing their artifact."""
from __future__ import annotations

import time

from ydbdoc_review.github.client import GitHubClient
from ydbdoc_review.github.errors import GitHubAPIError


def stop_previous_runs(
    gh: GitHubClient, owner: str, repo: str, source_pr: int, current_run_id: int,
    *, timeout: float = 120, poll_interval: float = 2,
) -> None:
    root = f"https://api.github.com/repos/{owner}/{repo}/actions"
    current = gh._request("GET", f"{root}/runs/{current_run_id}")
    workflow_id = current["workflow_id"]
    pending = []
    page = 1
    while True:
        data = gh._request("GET", f"{root}/workflows/{workflow_id}/runs",
                           params={"per_page": 100, "page": page})
        runs = data["workflow_runs"]
        for run in runs:
            if run["id"] == current_run_id or run["status"] == "completed":
                continue
            numbers = {pr["number"] for pr in run.get("pull_requests", [])}
            if source_pr not in numbers:
                # pull_request_target can omit pull_requests; use the source
                # branch only when both workers belong to the same workflow.
                if numbers or not current.get("head_branch") or run.get("head_branch") != current["head_branch"]:
                    continue
                if run.get("head_repository", {}).get("id") != current.get("head_repository", {}).get("id"):
                    continue
            if int(run["id"]) > current_run_id:
                raise RuntimeError("translation restart superseded by a newer run")
            pending.append(run["id"])
        if len(runs) < 100:
            break
        page += 1
    for run_id in pending:
        try:
            gh._request("POST", f"{root}/runs/{run_id}/cancel")
        except GitHubAPIError:
            # A worker can finish between discovery and the cancel request.
            if gh._request("GET", f"{root}/runs/{run_id}")["status"] != "completed":
                raise
    deadline = time.monotonic() + timeout
    while pending:
        pending = [run_id for run_id in pending
                   if gh._request("GET", f"{root}/runs/{run_id}")["status"] != "completed"]
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("previous translation stop not confirmed; branch was not deleted")
        time.sleep(poll_interval)
