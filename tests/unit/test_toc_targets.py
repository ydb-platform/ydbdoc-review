"""Tests for missing EN toc target checks (§6.83) and orphan pages (§6.117)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

from ydbdoc_review.navigation.toc import collect_toc_link_targets, resolve_toc_target_path
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    NavigationRunResult,
    PRTranslationResult,
    PairRunResult,
)
from ydbdoc_review.validation.toc_targets import (
    apply_orphan_toc_page_checks,
    apply_toc_target_checks,
    check_missing_toc_targets,
    check_orphan_translated_pages,
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


def test_check_orphan_translated_pages_flags_page_not_in_toc(tmp_path: Path):
    """Regression for #46569: translated page with no href from EN toc graph."""
    repo = _init_repo(tmp_path)
    root = "ydb/docs/en/core/toc_p.yaml"
    _write(
        repo,
        root,
        dedent("""
            items:
            - name: Concepts
              include: { mode: link, path: concepts/toc_i.yaml }
        """).strip()
        + "\n",
    )
    _write(
        repo,
        "ydb/docs/en/core/concepts/toc_i.yaml",
        dedent("""
            items:
            - name: Overview
              href: index.md
        """).strip()
        + "\n",
    )
    orphan = "ydb/docs/en/core/concepts/streaming-query/watermarks.md"
    orphans = check_orphan_translated_pages(
        {orphan},
        repo_path=repo,
        docs_root="ydb/docs",
    )
    assert orphan in orphans
    assert orphans[orphan][0].startswith("orphan_toc_page:")


def test_check_orphan_translated_pages_ok_when_linked_via_pending_parent(tmp_path: Path):
    """Pending parent toc + child toc wire the page into the root graph."""
    repo = _init_repo(tmp_path)
    root = "ydb/docs/en/core/toc_p.yaml"
    _write(
        repo,
        root,
        dedent("""
            items:
            - name: Concepts
              include: { mode: link, path: concepts/toc_i.yaml }
        """).strip()
        + "\n",
    )
    page = "ydb/docs/en/core/concepts/streaming-query/watermarks.md"
    parent_toc = dedent("""
        items:
        - name: Streaming
          href: streaming-query/index.md
          include:
            mode: link
            path: streaming-query/toc_p.yaml
    """).strip()
    child_toc = dedent("""
        items:
        - name: Watermarks
          href: watermarks.md
    """).strip()
    orphans = check_orphan_translated_pages(
        {page, "ydb/docs/en/core/concepts/streaming-query/index.md"},
        repo_path=repo,
        docs_root="ydb/docs",
        pending_toc_texts={
            "ydb/docs/en/core/concepts/toc_i.yaml": parent_toc,
            "ydb/docs/en/core/concepts/streaming-query/toc_p.yaml": child_toc,
        },
    )
    assert orphans == {}


def test_check_orphan_translated_pages_ignores_disconnected_pending_child_toc(
    tmp_path: Path,
):
    """Child toc pending but parent not wired → page still orphan (§6.116/§6.117)."""
    repo = _init_repo(tmp_path)
    root = "ydb/docs/en/core/toc_p.yaml"
    _write(
        repo,
        root,
        dedent("""
            items:
            - name: Concepts
              include: { mode: link, path: concepts/toc_i.yaml }
        """).strip()
        + "\n",
    )
    _write(
        repo,
        "ydb/docs/en/core/concepts/toc_i.yaml",
        dedent("""
            items:
            - name: Overview
              href: index.md
        """).strip()
        + "\n",
    )
    page = "ydb/docs/en/core/concepts/streaming-query/watermarks.md"
    child_toc = dedent("""
        items:
        - name: Watermarks
          href: watermarks.md
    """).strip()
    orphans = check_orphan_translated_pages(
        {page},
        repo_path=repo,
        docs_root="ydb/docs",
        pending_toc_texts={
            "ydb/docs/en/core/concepts/streaming-query/toc_p.yaml": child_toc,
        },
    )
    assert page in orphans


def test_check_orphan_uses_head_not_stale_worktree(tmp_path: Path):
    """§6.133: committed EN toc on HEAD wins over a main-like dirty worktree."""
    repo = _init_repo(tmp_path)
    root = "ydb/docs/en/core/toc_p.yaml"
    concepts = "ydb/docs/en/core/concepts/toc_i.yaml"
    page = "ydb/docs/en/core/concepts/json_search.md"
    good_root = dedent("""
        items:
        - name: Concepts
          include: { mode: link, path: concepts/toc_i.yaml }
    """).strip() + "\n"
    good_concepts = dedent("""
        items:
        - name: JSON search
          href: json_search.md
    """).strip() + "\n"
    stale_concepts = dedent("""
        items:
        - name: Overview
          href: index.md
    """).strip() + "\n"
    _write(repo, root, good_root)
    _write(repo, concepts, good_concepts)
    _write(repo, page, "# JSON\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "good toc"], cwd=repo, check=True)
    # Dirty worktree looks like main (no json_search href)
    _write(repo, concepts, stale_concepts)
    orphans = check_orphan_translated_pages(
        {page},
        repo_path=repo,
        docs_root="ydb/docs",
        extra_toc_paths={concepts},
    )
    assert orphans == {}


def test_check_orphan_translated_pages_skips_includes(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _write(repo, "ydb/docs/en/core/toc_p.yaml", "items: []\n")
    include_path = "ydb/docs/en/core/concepts/_includes/fragment.md"
    assert (
        check_orphan_translated_pages(
            {include_path},
            repo_path=repo,
            docs_root="ydb/docs",
        )
        == {}
    )


def test_apply_orphan_toc_page_checks_blocks_file_verdict(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _write(repo, "ydb/docs/en/core/toc_p.yaml", "items: []\n")
    en_path = "ydb/docs/en/core/concepts/streaming-query/watermarks.md"
    pair = DocPair(
        ru_path="ydb/docs/ru/core/concepts/streaming-query/watermarks.md",
        en_path=en_path,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=en_path,
        source_lang="ru",
        target_lang="en",
    )
    fr = FileTranslationResult(
        file_path=en_path,
        final_text="# Watermarks\n",
        segments_count=1,
        verdict="ok",
        prompt_version="v1",
    )
    result = PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, file_result=fr, target_text=fr.final_text)]
    )
    apply_orphan_toc_page_checks(result, repo_path=repo, docs_root="ydb/docs")
    assert fr.verdict == "blocked"
    assert any(m.startswith("orphan_toc_page:") for m in fr.heuristic_blocking)
