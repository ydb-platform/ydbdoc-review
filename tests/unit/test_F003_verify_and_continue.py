"""Contract tests for explicit verify and continue launches."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from ydbdoc_review.cli import app
from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import DocJobResult, run_doc_continue
from ydbdoc_review.ops.continue_cmd import parse_continue_instruction

runner = CliRunner()


def _job(mode: str) -> DocJobResult:
    return DocJobResult(mode=mode, pr_number=42, dry_run=True)


def test_F003_verify_and_continue_require_the_matching_label_event() -> None:
    verify = Path("examples/ydb-github-doc-verify-on-label.yml").read_text()
    continue_ = Path("examples/ydb-github-doc-continue-on-label.yml").read_text()

    assert "types: [labeled]" in verify
    assert "if: github.event.label.name == 'doc_verify'" in verify
    assert "types: [labeled]" in continue_
    assert "if: github.event.label.name == 'doc_continue'" in continue_


def test_F003_one_comment_supplies_continue_instruction() -> None:
    assert parse_continue_instruction("/ydbdoc continue fix glossary") == "fix glossary"


def test_F003_continue_without_instruction_stops_before_admission() -> None:
    config = load_config(env={"GITHUB_TOKEN": "token"})
    gh = MagicMock()
    gh.iter_issue_comments.return_value = []
    context = SimpleNamespace(head_ref="feature", labels=frozenset())

    with (
        patch("ydbdoc_review.github.workflow.load_config", return_value=config),
        patch("ydbdoc_review.github.workflow.GitHubClient", return_value=gh),
        patch("ydbdoc_review.github.workflow.pull_request_context", return_value=context),
        patch("ydbdoc_review.github.workflow.begin_ops_job") as begin_ops,
    ):
        result = run_doc_continue(
            repo_path=".",
            github_repo="o/r",
            pr_number=42,
            dry_run=True,
        )

    assert result.mode == "doc_continue"
    begin_ops.assert_not_called()


def test_F003_cli_continue_passes_explicit_instruction_without_comment(tmp_path: Path) -> None:
    with patch(
        "ydbdoc_review.cli.run_doc_continue",
        return_value=_job("doc_continue"),
    ) as continue_:
        result = runner.invoke(
            app,
            [
                "continue",
                "--repo",
                "o/r",
                "--pr",
                "42",
                "--repo-path",
                str(tmp_path),
                "--instruction",
                "fix glossary",
            ],
        )

    assert result.exit_code == 0, result.stdout
    assert continue_.call_args.kwargs["instruction"] == "fix glossary"


def test_F003_cli_verify_dispatches_verification() -> None:
    with patch(
        "ydbdoc_review.cli.run_doc_verify",
        return_value=_job("doc_verify"),
    ) as verify:
        result = runner.invoke(
            app,
            ["verify", "--repo", "o/r", "--pr", "42", "--dry-run"],
        )

    assert result.exit_code == 0, result.stdout
    verify.assert_called_once()
