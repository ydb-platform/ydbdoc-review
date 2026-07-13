"""Tests for missing EN toc target checks (§6.83)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

from ydbdoc_review.navigation.toc import collect_toc_link_targets, resolve_toc_target_path
from ydbdoc_review.pipeline.types import NavigationRunResult, PRTranslationResult
from ydbdoc_review.validation.toc_targets import (
    apply_toc_target_checks,
    check_missing_toc_targets,
)


def _init_repo(tmp_path: Path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    return str(repo)


def _write(repo: str, rel: str, text: str) -> None:
    path = Path(repo, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_collect_toc_link_targets_reads_include_on_href_item():
    toc = dedent("""
        items:
        - name: Spring
          href: spring/index.md
          include:
            mode: link
            path: spring/toc-spring.yaml
    """).strip()
    assert collect_toc_link_targets(toc) == [
        ("href", "spring/index.md"),
        ("include", "spring/toc-spring.yaml"),
    ]


def test_collect_toc_link_targets_reads_inline_include_only_item():
    toc = dedent("""
        items:
        - name: Overview
          href: index.md
        - include: { mode: link, path: toc_i.yaml }
    """).strip()
    assert collect_toc_link_targets(toc) == [
        ("href", "index.md"),
        ("include", "toc_i.yaml"),
    ]


def test_check_missing_toc_targets_detects_inline_include_child(tmp_path: Path):
    repo = _init_repo(tmp_path)
    en_toc = "ydb/docs/en/core/reference/sqs-api/toc_p.yaml"
    toc = dedent("""
        items:
        - name: Overview
          href: index.md
        - include: { mode: link, path: toc_i.yaml }
    """).strip()
    msgs = check_missing_toc_targets(en_toc, toc, repo_path=repo)
    assert len(msgs) == 2
    assert any("toc_i.yaml" in m for m in msgs)


    en_toc = "ydb/docs/en/core/devops/observability/toc_p.yaml"
    assert (
        resolve_toc_target_path(en_toc, "../../reference/observability/tracing/setup.md")
        == "ydb/docs/en/core/reference/observability/tracing/setup.md"
    )


def test_check_missing_toc_targets_detects_absent_include_yaml(tmp_path: Path):
    repo = _init_repo(tmp_path)
    en_toc = "ydb/docs/en/core/integrations/toc_i.yaml"
    toc = dedent("""
        items:
        - name: SQL translation
          href: sql-translation/index.md
          include:
            mode: link
            path: sql-translation/toc-sql-translation.yaml
    """).strip()
    msgs = check_missing_toc_targets(en_toc, toc, repo_path=repo)
    assert len(msgs) == 2
    assert all(m.startswith("missing_toc_target:") for m in msgs)
    assert "sql-translation/toc-sql-translation.yaml" in msgs[1]


def test_check_missing_toc_targets_allows_pending_diagnostics_file(tmp_path: Path):
    repo = _init_repo(tmp_path)
    en_toc = "ydb/docs/en/core/devops/observability/toc_p.yaml"
    toc = dedent("""
        items:
        - name: Diagnostics
          href: diagnostics.md
    """).strip()
    pending = {"ydb/docs/en/core/devops/observability/diagnostics.md"}
    assert (
        check_missing_toc_targets(
            en_toc, toc, repo_path=repo, pending_paths=pending
        )
        == []
    )


def test_apply_toc_target_checks_blocks_navigation_verdict(tmp_path: Path):
    repo = _init_repo(tmp_path)
    en_toc = "ydb/docs/en/core/integrations/toc_i.yaml"
    toc = dedent("""
        items:
        - name: SQL translation
          href: sql-translation/index.md
          include:
            mode: link
            path: sql-translation/toc-sql-translation.yaml
    """).strip()
    result = PRTranslationResult(
        navigation_results=[
            NavigationRunResult(
                ru_path="ydb/docs/ru/core/integrations/toc_i.yaml",
                en_path=en_toc,
                kind="toc",
                target_text=toc,
                verdict="ok",
            )
        ]
    )
    apply_toc_target_checks(result, repo_path=repo)
    assert result.navigation_results[0].verdict == "blocked"
    assert result.navigation_results[0].warnings
