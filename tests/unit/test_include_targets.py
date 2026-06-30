"""Tests for missing EN include target checks (§6.80)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import FileTranslationResult, PRTranslationResult, PairRunResult
from ydbdoc_review.validation.include_targets import (
    apply_include_target_checks,
    check_missing_locale_include_targets,
)


def _init_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    return str(repo)


def test_check_missing_locale_include_targets_detects_absent_file(tmp_path: Path):
    repo = _init_repo(tmp_path)
    en_path = (
        "ydb/docs/en/core/cli/export-import/_includes/export-s3.md"
    )
    en_text = "{% include [extra](export-additional-params.md) %}\n"
    msgs = check_missing_locale_include_targets(
        en_path, en_text, repo_path=repo, docs_root="ydb/docs"
    )
    assert len(msgs) == 1
    assert msgs[0].startswith("include_target:")
    assert "export-additional-params.md" in msgs[0]


def test_apply_include_target_checks_blocks_verdict(tmp_path: Path):
    repo = _init_repo(tmp_path)
    en_path = (
        "ydb/docs/en/core/cli/export-import/_includes/export-s3.md"
    )
    pair = DocPair(
        ru_path="ydb/docs/ru/core/cli/export-import/_includes/export-s3.md",
        en_path=en_path,
    )
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=pair.ru_path,
        target_path=en_path,
        source_lang="ru",
        target_lang="en",
    )
    fr = FileTranslationResult(
        file_path=en_path,
        final_text="{% include [extra](export-additional-params.md) %}\n",
        segments_count=1,
        verdict="ok",
        prompt_version="v1",
    )
    result = PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, file_result=fr)]
    )
    apply_include_target_checks(result, repo_path=repo, docs_root="ydb/docs")
    assert result.pair_results[0].file_result.verdict == "blocked"
    assert result.pair_results[0].file_result.heuristic_blocking
