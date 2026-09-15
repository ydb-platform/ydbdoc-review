"""F-145: an empty supported scope is a terminal, non-LLM outcome."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import run_doc_translate
from ydbdoc_review.ops.gates import GateResult
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.reporting.builder import ReportMeta, build_source_pr_comment


def _env() -> dict[str, str]:
    return {
        "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
        "YDBDOC_YC_FOLDER_ID": "b1",
        "YDBDOC_YC_API_KEY": "k",
        "GITHUB_TOKEN": "gh",
        "GITHUB_PUSH_TOKEN": "ghp",
        "YDBDOC_SKIP_OPS_GATES": "1",
    }


@pytest.fixture
def git_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    ru = repo / "ydb" / "docs" / "ru"
    ru.mkdir(parents=True)
    (ru / "a.md").write_text("Привет.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return str(repo)


def test_F145_empty_scope(git_repo: str):
    """Unsupported/excluded input must not reach any paid or publication stage."""
    checkout_sha = subprocess.check_output(
        ["git", "-C", git_repo, "rev-parse", "HEAD"], text=True
    ).strip()
    pull = {
        "title": "unsupported",
        "head": {
            "ref": "feature/docs",
            "sha": checkout_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": checkout_sha},
    }
    with (
        patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh,
        patch(
            "ydbdoc_review.github.workflow.begin_ops_job",
            return_value=(None, GateResult(ok=True), None),
        ),
        patch("ydbdoc_review.github.workflow.create_llm_client") as create_llm,
        patch("ydbdoc_review.github.workflow.run_pr_translation") as translate,
        patch("ydbdoc_review.github.workflow.run_doc_verify") as verify,
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            return_value=[("README.md", "modified")],
        ),
    ):
        mock_gh.return_value.get_pull.return_value = pull
        result = run_doc_translate(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=145,
            merge_base_with="HEAD",
            dry_run=True,
            config=load_config(env=_env()),
        )

    assert result.status == "no_supported_files"
    assert result.pr_result.publication_failure == "no_supported_files"
    assert result.pr_result.scope_reason
    assert result.pr_result.pair_results == []
    assert create_llm.call_count == 0
    assert translate.call_count == 0
    assert verify.call_count == 0
    assert result.committed is False
    assert result.pushed is False
    assert result.translation_pr_number is None


def test_F145_restart_report_is_not_alignment_claim():
    """A full restart must report the empty scope explicitly, not stale no-op."""
    result = PRTranslationResult(
        publication_failure="no_supported_files",
        scope_reason="файлы README.md исключены фильтрами",
    )
    body = build_source_pr_comment(
        result,
        translation_pr_number=None,
        meta=ReportMeta(mode="doc_translate", report_number=1, elapsed_s=0),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}),
    )
    assert "Нет файлов для перевода" in body
    assert "модели не запускались" in body
    assert "всё уже согласовано" not in body
    assert "₽0.00" in body
