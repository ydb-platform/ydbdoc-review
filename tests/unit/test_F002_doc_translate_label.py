"""F-002 label contract tests for ``doc_translate``."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.workflow import run_doc_translate
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import FileTranslationResult, PairRunResult, PRTranslationResult


def _env() -> dict[str, str]:
    return {
        "YDBDOC_MODEL_PROVIDER": "yandex_cloud",
        "YDBDOC_YC_FOLDER_ID": "b1",
        "YDBDOC_YC_API_KEY": "k",
        "GITHUB_TOKEN": "gh",
        "GITHUB_PUSH_TOKEN": "ghp",
    }


@pytest.fixture(autouse=True)
def _f002_ops_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YDBDOC_ALLOWED_ACTORS", "night-operator")
    monkeypatch.setenv("GITHUB_ACTOR", "night-operator")
    monkeypatch.setenv("YDBDOC_DAILY_BUDGET_RUB", "5000")
    monkeypatch.setenv("YDBDOC_RUNS_LEDGER", "memory")
    monkeypatch.setenv("YDBDOC_TRANSCRIPT_BACKEND", "memory")


@pytest.fixture
def git_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    return str(repo)


def _fake_pr_result() -> PRTranslationResult:
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
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
    return PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, target_text="Hello.\n", file_result=fr)]
    )


def test_F002_doc_translate_open_pr_runs_full_translation(git_repo: str):
    repo = Path(git_repo)
    (repo / "ydb" / "docs" / "ru").mkdir(parents=True)
    (repo / "ydb" / "docs" / "ru" / "a.md").write_text("Привет, мир.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "docs"], cwd=git_repo, check=True)
    source_sha = subprocess.check_output(
        ["git", "-C", git_repo, "rev-parse", "HEAD"], text=True
    ).strip()

    pull = {
        "title": "open docs",
        "head": {
            "ref": "feature/docs",
            "sha": source_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": source_sha},
        "state": "open",
        "merged": False,
    }

    with (
        patch("ydbdoc_review.github.workflow._run_verify_pairs") as verify_pairs,
        patch(
            "ydbdoc_review.github.workflow.run_pr_translation",
            return_value=_fake_pr_result(),
        ) as translate,
        patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh,
        patch(
            "ydbdoc_review.github.workflow.create_llm_client",
            return_value=MagicMock(),
        ) as llm_client,
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            return_value=[("ydb/docs/ru/a.md", "modified")],
        ),
    ):
        mock_gh.return_value.get_pull.return_value = pull
        result = run_doc_translate(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=7,
            merge_base_with="HEAD",
            dry_run=True,
            config=load_config(env=_env()),
        )

    assert result.blocked is False
    assert result.pr_result.translated_count == 1
    translate.assert_called_once()
    verify_pairs.assert_not_called()
    llm_client.assert_called_once()


def test_F002_doc_translate_merged_pr_uses_full_translation(git_repo: str):
    repo = Path(git_repo)
    (repo / "ydb" / "docs" / "ru").mkdir(parents=True)
    (repo / "ydb" / "docs" / "ru" / "a.md").write_text("Привет, мир.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "docs"], cwd=git_repo, check=True)
    (repo / "ydb" / "docs" / "ru" / "a.md").write_text("Привет, мир!\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "merged docs head"], cwd=git_repo, check=True)
    merged_sha = subprocess.check_output(
        ["git", "-C", git_repo, "rev-parse", "HEAD"], text=True
    ).strip()

    pull = {
        "title": "merged docs",
        "head": {
            "ref": "feature/docs",
            "sha": merged_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": merged_sha},
        "state": "closed",
        "merged": True,
        "merge_commit_sha": merged_sha,
    }

    with (
        patch("ydbdoc_review.github.workflow._run_verify_pairs") as verify_pairs,
        patch(
            "ydbdoc_review.github.workflow.run_pr_translation",
            return_value=_fake_pr_result(),
        ) as translate,
        patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh,
        patch(
            "ydbdoc_review.github.workflow.create_llm_client",
            return_value=MagicMock(),
        ) as llm_client,
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            return_value=[("ydb/docs/ru/a.md", "modified")],
        ),
    ):
        mock_gh.return_value.get_pull.return_value = pull
        result = run_doc_translate(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=7,
            merge_base_with="HEAD",
            dry_run=True,
            config=load_config(env=_env()),
        )

    assert result.blocked is False
    assert result.pr_result.translated_count == 1
    translate.assert_called_once()
    verify_pairs.assert_not_called()
    llm_client.assert_called_once()


def test_F002_doc_translate_closed_unmerged_blocked_before_llm(git_repo: str):
    repo = Path(git_repo)
    (repo / "ydb" / "docs" / "ru").mkdir(parents=True)
    (repo / "ydb" / "docs" / "ru" / "a.md").write_text("Привет, мир.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "docs"], cwd=git_repo, check=True)
    source_sha = subprocess.check_output(
        ["git", "-C", git_repo, "rev-parse", "HEAD"], text=True
    ).strip()

    pull = {
        "title": "closed docs",
        "head": {
            "ref": "feature/docs",
            "sha": source_sha,
            "repo": {"clone_url": "https://github.com/o/r.git", "full_name": "o/r"},
        },
        "base": {"ref": "main", "sha": source_sha},
        "state": "closed",
        "merged": False,
    }

    with (
        patch("ydbdoc_review.github.workflow._run_verify_pairs") as verify_pairs,
        patch(
            "ydbdoc_review.github.workflow.run_pr_translation",
            side_effect=AssertionError("run_pr_translation must not be used"),
        ) as translate,
        patch(
            "ydbdoc_review.github.workflow.create_llm_client",
            side_effect=AssertionError("LLM client must not be created"),
        ) as llm_client,
        patch(
            "ydbdoc_review.github.workflow.list_pr_file_changes_api",
            side_effect=AssertionError("PR file diff must not be loaded"),
        ) as file_changes,
        patch("ydbdoc_review.github.workflow.GitHubClient") as mock_gh,
        patch(
            "ydbdoc_review.github.workflow._safe_post_issue_comment",
            return_value="comment",
        ) as notify,
    ):
        mock_gh.return_value.get_pull.return_value = pull
        result = run_doc_translate(
            repo_path=git_repo,
            github_repo="o/r",
            pr_number=7,
            merge_base_with="HEAD",
            dry_run=False,
            config=load_config(env=_env()),
        )

    assert result.blocked is True
    verify_pairs.assert_not_called()
    llm_client.assert_not_called()
    file_changes.assert_not_called()
    translate.assert_not_called()
    notify.assert_called_once()
