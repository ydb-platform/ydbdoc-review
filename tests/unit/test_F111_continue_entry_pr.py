"""F-111: continue is admitted only through supported PR entry points."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import DocJobResult, run_doc_continue
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.ops.job_state import ContinuabilityState

SOURCE_PR = 7
ENTRY_PR = 99


def _config():
    return load_config(
        env={
            "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
            "YDBDOC_YC_FOLDER_ID": "folder",
            "YDBDOC_YC_API_KEY": "key",
            "GITHUB_TOKEN": "token",
            "GITHUB_PUSH_TOKEN": "push-token",
        }
    )


def _pull(
    head_ref: str,
    *,
    state: str = "open",
    merged: bool = False,
    service: bool = True,
) -> dict[str, object]:
    return {
        "title": "Continue entry",
        "body": "",
        "state": state,
        "merged": merged,
        "user": {"login": "github-actions[bot]" if service else "author"},
        "head": {
            "ref": head_ref,
            "sha": "a" * 40,
            "repo": {
                "clone_url": "https://github.com/o/r.git",
                "full_name": "o/r",
            },
        },
        "base": {"ref": "main", "sha": "b" * 40},
    }


def _continuability(translation_pr: int | None) -> ContinuabilityState:
    return ContinuabilityState(
        continuable=True,
        unfinished_stage="verify",
        fixed_shas={"merge_base": "b" * 40, "head": "a" * 40},
        source_pr=SOURCE_PR,
        translation_pr=translation_pr,
    )


@pytest.mark.parametrize(
    ("entry_pr", "head_ref", "service", "artifact_pr", "runner"),
    [
        (ENTRY_PR, f"ydbdoc-review/pr-{SOURCE_PR}", True, ENTRY_PR, "translate"),
        (ENTRY_PR, f"ydbdoc-review/verify-{SOURCE_PR}", True, ENTRY_PR, "verify"),
        (SOURCE_PR, "feature/docs", False, None, "translate"),
    ],
)
def test_F111_open_and_source(
    entry_pr: int,
    head_ref: str,
    service: bool,
    artifact_pr: int | None,
    runner: str,
) -> None:
    """Open service PRs and saved source no-artifacts accept a new instruction."""
    github = MagicMock()
    github.get_pull.return_value = _pull(head_ref, service=service)
    ops_ctx = SimpleNamespace(parent_run_id="parent-run", store=None)
    translated = DocJobResult(
        mode="doc_translate",
        pr_number=SOURCE_PR,
        source_pr_number=SOURCE_PR,
        translation_pr_number=101,
    )
    verified = DocJobResult(
        mode="doc_verify",
        pr_number=ENTRY_PR,
        source_pr_number=SOURCE_PR,
        translation_pr_number=ENTRY_PR,
    )

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=github),
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(ops_ctx, GateResult(ok=True), None),
        ) as begin,
        patch(
            "ydbdoc_review.github.workflow._load_continuability_for_continue",
            return_value=_continuability(artifact_pr),
        ),
        patch(
            "ydbdoc_review.github.workflow.run_doc_translate",
            return_value=translated,
        ) as translate,
        patch(
            "ydbdoc_review.github.workflow.run_doc_verify",
            return_value=verified,
        ) as verify,
    ):
        result = run_doc_continue(
            repo_path="/repo",
            github_repo="o/r",
            pr_number=entry_pr,
            merge_base_with="origin/main",
            dry_run=True,
            config=_config(),
            instruction="Reconsider Analyze and publish the translation",
        )

    begin.assert_called_once()
    assert begin.call_args.kwargs["source_pr"] == SOURCE_PR
    assert begin.call_args.kwargs["translation_pr"] == artifact_pr
    if runner == "translate":
        translate.assert_called_once()
        assert translate.call_args.kwargs["pr_number"] == SOURCE_PR
        assert translate.call_args.kwargs["continue_feedback"] == (
            "Reconsider Analyze and publish the translation"
        )
        verify.assert_not_called()
        assert result.translation_pr_number == 101
    else:
        verify.assert_called_once()
        assert verify.call_args.kwargs["pr_number"] == ENTRY_PR
        translate.assert_not_called()
        assert result.translation_pr_number == ENTRY_PR
    assert result.mode == "doc_continue"


@pytest.mark.parametrize("prefix", ["ydbdoc-review/pr-", "ydbdoc-review/verify-"])
@pytest.mark.parametrize(("state", "merged"), [("closed", False), ("closed", True)])
def test_F111_closed_result(prefix: str, state: str, merged: bool) -> None:
    """Closed and merged result PRs reject mutation and point to fresh Analyze."""
    github = MagicMock()
    github.get_pull.return_value = _pull(
        f"{prefix}{SOURCE_PR}", state=state, merged=merged
    )
    ops_ctx = SimpleNamespace(parent_run_id="parent-run", store=None)
    continued = DocJobResult(
        mode="doc_translate",
        pr_number=SOURCE_PR,
        source_pr_number=SOURCE_PR,
        translation_pr_number=ENTRY_PR,
    )

    with (
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=github),
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(ops_ctx, GateResult(ok=True), None),
        ) as begin,
        patch(
            "ydbdoc_review.github.workflow._load_continuability_for_continue",
            return_value=_continuability(ENTRY_PR),
        ),
        patch("ydbdoc_review.github.workflow._persist_continuability") as persist,
        patch(
            "ydbdoc_review.github.workflow.run_doc_translate",
            return_value=continued,
        ) as translate,
        patch(
            "ydbdoc_review.github.workflow.run_doc_verify",
            return_value=continued,
        ) as verify,
    ):
        result = run_doc_continue(
            repo_path="/repo",
            github_repo="o/r",
            pr_number=ENTRY_PR,
            config=_config(),
            instruction="Change the translation",
        )

    assert result.blocked is True
    assert result.source_pr_number == SOURCE_PR
    assert result.translation_pr_number == ENTRY_PR
    begin.assert_not_called()
    persist.assert_not_called()
    translate.assert_not_called()
    verify.assert_not_called()
    github.post_issue_comment.assert_called_once()
    body = github.post_issue_comment.call_args.args[3]
    assert f"PR #{SOURCE_PR}" in body
    assert "новый `doc_translate`" in body
    assert "Analyze" in body
