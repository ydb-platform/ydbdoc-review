"""F-004 contract tests for Action, CLI, and the unified job entry point."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ydbdoc_review import cli
from ydbdoc_review.github import workflow


def test_F004_action_and_cli_expose_the_same_explicit_inputs_for_all_modes():
    action = Path("action.yml").read_text(encoding="utf-8")

    assert 'repo:\n    description:' in action
    assert 'pr:\n    description:' in action
    assert 'mode:\n    description:' in action
    assert 'required: true' in action
    for mode in ("run", "verify", "continue"):
        assert f'"{mode}"' in action

    for command in (cli.run, cli.verify, cli.continue_, cli.job):
        repo_path = inspect.signature(command).parameters["repo_path"]
        assert repo_path.default is inspect.Parameter.empty

    with pytest.raises(cli.typer.BadParameter):
        cli._resolve_repo_path(None)


def test_F004_job_dispatches_explicitly_to_each_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: list[tuple[str, dict[str, object]]] = []

    class Result:
        mode = "doc_translate"
        pr_result = type("PRResult", (), {"publication_impact": None})()

    def fake(name: str):
        def invoke(**kwargs: object) -> Result:
            calls.append((name, kwargs))
            return Result()

        return invoke

    monkeypatch.setattr(cli, "run_doc_translate", fake("translate"))
    monkeypatch.setattr(cli, "run_doc_verify", fake("verify"))
    monkeypatch.setattr(cli, "run_doc_continue", fake("continue"))
    monkeypatch.setattr(cli, "job_requires_nonzero_exit", lambda *args, **kwargs: False)
    monkeypatch.setattr(cli, "_print_job_summary", lambda *args, **kwargs: None)

    for mode in ("run", "verify", "continue"):
        cli.job(mode, "o/r", 17, tmp_path)

    assert [name for name, _ in calls] == ["translate", "verify", "continue"]
    assert all(kwargs["github_repo"] == "o/r" for _, kwargs in calls)
    assert all(kwargs["pr_number"] == 17 for _, kwargs in calls)
    assert all(kwargs["repo_path"] == str(tmp_path.resolve()) for _, kwargs in calls)


def test_F004_verify_does_not_derive_source_pr_from_arbitrary_title():
    assert "parse_source_pr_from_text" not in inspect.getsource(workflow.run_doc_verify)
