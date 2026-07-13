"""Tests for CLI commands."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from ydbdoc_review.cli import app
from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import DocJobResult
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import FileTranslationResult, PRTranslationResult, PairRunResult
from ydbdoc_review.pipeline.analyze import PairPlan

runner = CliRunner()


def _fake_doc_job(mode: str = "doc_translate") -> DocJobResult:
    pair = DocPair(ru_path="ydb/docs/ru/a.md", en_path="ydb/docs/en/a.md", ru_changed=True)
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    fr = FileTranslationResult(
        file_path=pair.en_path,
        final_text="Hello.\n",
        segments_count=1,
        verdict="ok",
        prompt_version="v1",
    )
    pr_result = PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, target_text="Hello.\n", file_result=fr)]
    )
    return DocJobResult(mode=mode, pr_number=7, pr_result=pr_result, dry_run=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    return repo


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.stdout


def test_list_models_config_only():
    result = runner.invoke(app, ["list-models"])
    assert result.exit_code == 0
    assert "translate" in result.stdout


def test_extract_json(tmp_path: Path):
    md = tmp_path / "sample.md"
    md.write_text("# Title\n\nHello world.\n", encoding="utf-8")
    result = runner.invoke(app, ["extract", str(md)])
    assert result.exit_code == 0
    assert "s0001" in result.stdout or '"id"' in result.stdout


def test_extract_text_format(tmp_path: Path):
    md = tmp_path / "sample.md"
    md.write_text("Plain.\n", encoding="utf-8")
    result = runner.invoke(app, ["extract", str(md), "--format", "text"])
    assert result.exit_code == 0


@patch("ydbdoc_review.cli.run_doc_translate", return_value=_fake_doc_job("doc_translate"))
def test_cli_run_dry_run(mock_run, git_repo: Path):
    result = runner.invoke(
        app,
        [
            "run",
            "--repo",
            "o/r",
            "--pr",
            "7",
            "--repo-path",
            str(git_repo),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.stdout
    mock_run.assert_called_once()
    assert "Pairs processed" in result.stdout


@patch("ydbdoc_review.cli.run_doc_verify", return_value=_fake_doc_job("doc_verify"))
def test_cli_verify_dry_run(mock_verify, git_repo: Path):
    result = runner.invoke(
        app,
        [
            "verify",
            "--repo",
            "o/r",
            "--pr",
            "42",
            "--repo-path",
            str(git_repo),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.stdout
    mock_verify.assert_called_once()
    assert "Pairs processed" in result.stdout


@patch("ydbdoc_review.cli.translate_file")
@patch("ydbdoc_review.cli.create_llm_client")
@patch("ydbdoc_review.cli.load_config")
def test_cli_translate_file_stdout(
    mock_load_config,
    mock_create_client,
    mock_translate_file,
    tmp_path: Path,
):
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    mock_load_config.return_value = cfg
    mock_create_client.return_value = MagicMock()
    mock_translate_file.return_value = FileTranslationResult(
        file_path="sample.md",
        final_text="Translated.\n",
        segments_count=1,
        verdict="ok",
        prompt_version="v1",
    )

    md = tmp_path / "sample.md"
    md.write_text("Привет.\n", encoding="utf-8")
    result = runner.invoke(app, ["translate-file", str(md)])

    assert result.exit_code == 0, result.stdout
    assert result.stdout == "Translated.\n"
    mock_translate_file.assert_called_once()


@patch("ydbdoc_review.cli.translate_file")
@patch("ydbdoc_review.cli.create_llm_client")
@patch("ydbdoc_review.cli.load_config")
def test_cli_translate_file_writes_output(
    mock_load_config,
    mock_create_client,
    mock_translate_file,
    tmp_path: Path,
):
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    mock_load_config.return_value = cfg
    mock_create_client.return_value = MagicMock()
    mock_translate_file.return_value = FileTranslationResult(
        file_path="sample.md",
        final_text="Out.\n",
        segments_count=1,
        verdict="warnings",
        prompt_version="v1",
    )

    md = tmp_path / "sample.md"
    out = tmp_path / "out.md"
    md.write_text("Text.\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["translate-file", str(md), "-o", str(out), "--no-critic"],
    )

    assert result.exit_code == 0, result.stdout
    assert out.read_text(encoding="utf-8") == "Out.\n"
    assert "verdict=warnings" in result.stdout


@patch("ydbdoc_review.cli.load_config")
def test_cli_translate_file_missing_credentials(mock_load_config):
    cfg = load_config(env={})
    mock_load_config.return_value = cfg
    with patch("ydbdoc_review.cli.create_llm_client") as mock_create:
        mock_create.side_effect = RuntimeError("missing credentials")
        result = runner.invoke(app, ["translate-file", "missing.md"])
    assert result.exit_code == 1
    assert "Error" in result.stdout or "missing" in result.stdout.lower()
