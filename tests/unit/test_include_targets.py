"""Tests for missing EN include target checks (§6.80)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import FileTranslationResult, PRTranslationResult, PairRunResult
from ydbdoc_review.validation.include_targets import (
    apply_include_parity_repair,
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


def test_include_parity_detects_missing_career_include(tmp_path: Path):
    from ydbdoc_review.validation.include_targets import (
        check_include_parity,
        repair_missing_includes,
    )

    repo = _init_repo(tmp_path)
    ru_path = "ydb/docs/ru/core/contributor/hive-booting.md"
    en_inc = "ydb/docs/en/core/contributor/_includes/career.md"
    Path(repo, "ydb/docs/en/core/contributor/_includes").mkdir(parents=True)
    Path(repo, en_inc).write_text("note\n", encoding="utf-8")

    ru = "# Hive\n\nBoot.\n\n{% include [career](./_includes/career.md) %}\n"
    en = "# Hive\n\nBoot.\n"
    msgs = check_include_parity(ru, en, source_file=ru_path)
    assert msgs and msgs[0].startswith("include_parity:")
    assert "career.md" in msgs[0]

    repaired = repair_missing_includes(
        ru,
        en,
        source_file=ru_path,
        repo_path=repo,
    )
    assert "{% include [career](./_includes/career.md) %}" in repaired
    assert check_include_parity(ru, repaired, source_file=ru_path) == []


def test_apply_include_parity_repair_uses_pair_source_text_not_checkout(
    tmp_path: Path,
):
    """§6.154: do not demand post-merge includes from checkout main."""
    repo = _init_repo(tmp_path)
    ru_path = "ydb/docs/ru/core/cli/import-s3.md"
    en_path = "ydb/docs/en/core/cli/import-s3.md"
    Path(repo, "ydb/docs/ru/core/cli").mkdir(parents=True)
    Path(repo, "ydb/docs/en/core/cli").mkdir(parents=True)
    # Checkout (= main) has a post-merge include; merge-commit RU does not.
    Path(repo, ru_path).write_text(
        "# Import\n\n{% include [broker](import-resource-broker-note.md) %}\n",
        encoding="utf-8",
    )
    merge_ru = "# Import\n\nBody.\n"
    en = "# Import\n\nBody EN.\n"
    pair = DocPair(ru_path=ru_path, en_path=en_path)
    plan = PairPlan(
        pair=pair,
        action="critic_only",
        source_path=ru_path,
        target_path=en_path,
        source_lang="ru",
        target_lang="en",
    )
    fr = FileTranslationResult(
        file_path=en_path,
        final_text=en,
        segments_count=1,
        verdict="ok",
        prompt_version="v1",
    )
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(plan=plan, file_result=fr, source_text=merge_ru)
        ]
    )
    apply_include_parity_repair(result, repo_path=repo, docs_root="ydb/docs")
    assert result.pair_results[0].file_result.verdict == "ok"
    assert not any(
        m.startswith("include_parity:")
        for m in result.pair_results[0].file_result.heuristic_blocking
    )


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
