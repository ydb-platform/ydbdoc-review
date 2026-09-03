"""Unit tests for tip-newer yellow overwrite policy (REQUIREMENTS §10 / §12 / P4)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.git_ops import git_commit_paths, write_text
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pair import run_pair_plan
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan, plan_pair_heuristic
from ydbdoc_review.pipeline.completeness import completeness_gaps
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.tip_newer import (
    apply_tip_newer_policy,
    blob_id_at_ref,
    detect_tip_newer_for_pair,
    format_tip_newer_warning,
    tip_newer_warnings_block_publish,
)
from ydbdoc_review.pipeline.types import FileTranslationResult, PairRunResult, PRTranslationResult
from ydbdoc_review.translation.glossary import load_glossary


def _git(repo: str, *args: str) -> str:
    return subprocess.check_output(["git", "-C", repo, *args], text=True).strip()


def _commit(repo: str, message: str) -> str:
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", message], check=True)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def tip_newer_repo(tmp_path: Path) -> tuple[str, str, str]:
    """Repo with source-PR merge SHA then tip EN (and optional RU) edits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)

    ru = "ydb/docs/ru/core/page.md"
    en = "ydb/docs/en/core/page.md"
    write_text(str(repo), ru, "# Страница\n\nТекст RU source.\n")
    write_text(str(repo), en, "# Page\n\nEN source.\n")
    source_sha = _commit(str(repo), "source pr merge")

    write_text(str(repo), en, "# Page\n\nEN tip-only edit after source PR.\n")
    tip_sha = _commit(str(repo), "tip en newer")
    return str(repo), source_sha, tip_sha


def _pair() -> DocPair:
    return DocPair(
        ru_path="ydb/docs/ru/core/page.md",
        en_path="ydb/docs/en/core/page.md",
        ru_changed=True,
        en_changed=False,
    )


def test_en_newer_emits_warning_with_path_and_commits(
    tip_newer_repo: tuple[str, str, str],
) -> None:
    repo, source_sha, tip_sha = tip_newer_repo
    content = PairContent(
        pair=_pair(),
        ru_text="# Страница\n\nТекст RU source.\n",
        en_text="# Page\n\nEN tip-only edit after source PR.\n",
    )
    findings = detect_tip_newer_for_pair(
        repo, content, source_ref=source_sha, tip_ref=tip_sha
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.path == "ydb/docs/en/core/page.md"
    assert finding.kind == "en_modified"
    assert finding.source_blob != finding.tip_blob
    assert finding.commits  # tip commit that touched EN

    warning = format_tip_newer_warning(finding)
    assert "ydb/docs/en/core/page.md" in warning
    assert "source=" in warning and "tip=" in warning
    assert finding.commits[0] in warning
    assert "не блокирует commit/push" in warning


def test_en_newer_force_overwrite_and_does_not_block_push(
    tip_newer_repo: tuple[str, str, str],
) -> None:
    repo, source_sha, tip_sha = tip_newer_repo
    content = PairContent(
        pair=_pair(),
        ru_text="# Страница\n\nТекст RU source.\n",
        en_text="# Page\n\nEN source.\n",
        ru_base_text="# Страница\n\nТекст RU base.\n",
        en_base_text="# Page\n\nEN source.\n",
    )
    updated, warnings = apply_tip_newer_policy(
        repo,
        [content],
        source_ref=source_sha,
        tip_ref=tip_sha,
    )
    assert len(updated) == 1
    assert updated[0].force_full_overwrite is True
    assert updated[0].en_text == "# Page\n\nEN tip-only edit after source PR.\n"
    assert warnings
    assert all("не блокирует commit/push" in w for w in warnings)
    assert tip_newer_warnings_block_publish(warnings) is False

    # Yellow tip-newer notices must never appear as completeness blockers.
    pr_result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan_pair_heuristic(updated[0]),
                target_text="# Page\n\nFull translate of current RU.\n",
                file_result=FileTranslationResult(
                    file_path="ydb/docs/en/core/page.md",
                    final_text="# Page\n\nFull translate of current RU.\n",
                    segments_count=1,
                    verdict="ok",
                    prompt_version="test",
                ),
            )
        ],
        yellow_warnings=list(warnings),
    )
    gaps = completeness_gaps(
        [("ydb/docs/ru/core/page.md", "modified")],
        pr_result,
    )
    assert gaps == []
    for w in warnings:
        assert w not in gaps
        assert "ydb/docs/en/core/page.md" not in gaps

    # Push path: empty completeness gaps → commit still runs.
    write_text(repo, "ydb/docs/en/core/page.md", "# Page\n\nFull translate of current RU.\n")
    committed = git_commit_paths(
        repo,
        ["ydb/docs/en/core/page.md"],
        "overwrite en after tip-newer warn",
        "test",
        "t@example.com",
    )
    assert committed is True
    assert tip_newer_warnings_block_publish(pr_result.yellow_warnings) is False


def test_force_full_overwrite_skips_en_preserve_stitch() -> None:
    """EN tip-newer + force_full_overwrite must not keep tip EN via preserve."""
    tip_en = "# Page\n\nEN tip-only edit after source PR.\n"
    tip_ru = "# Страница\n\nТекст RU source.\n"
    content = PairContent(
        pair=_pair(),
        ru_text=tip_ru,
        en_text=tip_en,
        ru_base_text="# Страница\n\nТекст RU base (href delta).\n",
        en_base_text="# Page\n\nEN source.\n",
        force_full_overwrite=True,
    )
    plan = PairPlan(
        pair=content.pair,
        action="translate_to_en",
        source_path=content.pair.ru_path,
        target_path=content.pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="full overwrite",
    )
    translated = "# Page\n\nFull translate overwrite.\n"
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"})
    parent = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
    )

    class _FakeHarness:
        def __init__(self, _profile):
            pass

        def run(self, state, ctx):
            result = MagicMock()
            result.final_text = translated
            result.differential_meta = {
                "mode": "full",
                "semantic_noop": False,
                "enabled": False,
            }
            result.verdict = "ok"
            result.critic_initial = None
            result.critic_applied = []
            result.critic_skipped = []
            result.critic_unresolved = None
            result.heuristic_blocking = []
            result.heuristic_warnings = []
            result.heuristic_info = []
            result.manual_actions = []
            result.segment_locations = {}
            result.segment_lines = {}
            result.segment_excerpts = {}
            result.segment_source_excerpts = {}
            result.segment_alignment_error = None
            result.link_contract_issues = ()
            return result

    with patch("ydbdoc_review.harness.pair.FileHarness", _FakeHarness):
        with patch(
            "ydbdoc_review.harness.pair._try_deterministic_en_preserve",
            return_value=tip_en,
        ) as preserve:
            result = run_pair_plan(content, plan, parent, cache={})
            preserve.assert_not_called()
    assert result.target_text == translated
    assert result.target_text != tip_en

def test_bilingual_source_pr_still_skips_model() -> None:
    """§10 hard case: source PR itself changed both RU and EN → skip (blocker)."""
    pair = DocPair(
        ru_path="ydb/docs/ru/core/page.md",
        en_path="ydb/docs/en/core/page.md",
        ru_changed=True,
        en_changed=True,
    )
    plan = plan_pair_heuristic(
        PairContent(pair=pair, ru_text="# RU\n", en_text="# EN\n")
    )
    assert plan.action == "skip"
    assert "§6.76" in plan.summary


def test_tip_ru_newer_uses_tip_ru_body(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    write_text(str(repo), "ydb/docs/ru/core/page.md", "# RU source\n")
    write_text(str(repo), "ydb/docs/en/core/page.md", "# EN source\n")
    source_sha = _commit(str(repo), "source")
    write_text(str(repo), "ydb/docs/ru/core/page.md", "# RU tip newer\n")
    tip_sha = _commit(str(repo), "tip ru")

    content = PairContent(
        pair=_pair(),
        ru_text="# RU source\n",
        en_text="# EN source\n",
    )
    updated, warnings = apply_tip_newer_policy(
        str(repo),
        [content],
        source_ref=source_sha,
        tip_ref=tip_sha,
    )
    assert updated[0].ru_text == "# RU tip newer\n"
    assert updated[0].force_full_overwrite is True
    assert any("русский файл" in w for w in warnings)
    assert blob_id_at_ref(str(repo), tip_sha, "ydb/docs/ru/core/page.md")
