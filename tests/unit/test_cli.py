"""Tests for CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from ydbdoc_review.cli import app

runner = CliRunner()


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
