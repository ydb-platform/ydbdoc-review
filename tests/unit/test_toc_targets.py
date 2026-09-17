"""Tests for missing EN toc target checks (legacy (non-normative)) and orphan pages (legacy (non-normative))."""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from ydbdoc_review.navigation.toc import collect_toc_link_targets, resolve_toc_target_path
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
)
from ydbdoc_review.validation.include_targets import apply_include_target_checks
from ydbdoc_review.validation.toc_targets import (
    apply_orphan_toc_page_checks,
    apply_toc_target_checks,
    check_missing_toc_targets,
    check_orphan_translated_pages,
    find_locale_pages_missing_from_toc,
    find_pages_missing_from_toc,
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


def _pair_plan(en_path: str) -> PairPlan:
    pair = DocPair(
        ru_path=en_path.replace("/en/", "/ru/", 1),
        en_path=en_path,
    )
    return PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=en_path,
        source_lang="ru",
        target_lang="en",
    )


def _translated_run(en_path: str, final_text: str) -> PairRunResult:
    plan = _pair_plan(en_path)
    file_result = FileTranslationResult(
        file_path=en_path,
        final_text=final_text,
        segments_count=1,
        verdict="ok",
        prompt_version="v1",
    )
    return PairRunResult(
        plan=plan,
        file_result=file_result,
        target_text=final_text,
    )


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


def test_collect_toc_link_targets_odd_nested_indent():
    """Diplodoc sometimes nests with odd indent (5 spaces); orphans must still see href (legacy (non-normative))."""
    toc = dedent("""
        items:
        - name: Hive
          href: hive.md
          items:
           - name: Tablet Boot Process
             href: hive-booting.md
        - name: Other
          href: other.md
    """).strip()
    hrefs = [p for kind, p in collect_toc_link_targets(toc) if kind == "href"]
    assert hrefs == ["hive.md", "hive-booting.md", "other.md"]


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
    """Child toc pending but parent not wired → page still orphan (legacy (non-normative)/legacy (non-normative))."""
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
    """legacy (non-normative): committed EN toc on HEAD wins over a main-like dirty worktree."""
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


def test_apply_orphan_toc_page_checks_exempts_redirect_tombstone(tmp_path: Path):
    """#45949 / legacy (non-normative): redirect from-paths are not orphan_toc_page blockers."""
    repo = _init_repo(tmp_path)
    _write(repo, "ydb/docs/en/core/toc_p.yaml", "items: []\n")
    en_path = "ydb/docs/en/core/maintenance/manual/dynamic-config.md"
    pair = DocPair(
        ru_path="ydb/docs/ru/core/maintenance/manual/dynamic-config.md",
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
        final_text="# Dynamic config\n",
        segments_count=1,
        verdict="ok",
        prompt_version="v1",
    )
    result = PRTranslationResult(
        pair_results=[PairRunResult(plan=plan, file_result=fr, target_text=fr.final_text)]
    )
    orphans = apply_orphan_toc_page_checks(
        result,
        repo_path=repo,
        docs_root="ydb/docs",
        exempt_en_paths={en_path},
    )
    assert orphans == []
    assert fr.verdict == "ok"
    assert fr.heuristic_blocking == []


def test_pr_51079_include_only_assets_are_reachable_from_pending_candidate(
    tmp_path: Path,
):
    repo = _init_repo(tmp_path)
    root_toc = "ydb/docs/en/core/toc_p.yaml"
    _write(
        repo,
        root_toc,
        "items:\n- name: Security\n  include:\n    mode: link\n"
        "    path: security/toc_p.yaml\n",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)

    security = "ydb/docs/en/core/security"
    caching = f"{security}/caching-authentication-results.md"
    user_token = f"{security}/_assets/user-token.md"
    lifecycle = f"{security}/_assets/user-token-lifecycle.md"
    ordinary_pages = [f"{security}/page-{index}.md" for index in range(1, 6)]
    caching_text = (
        "# Caching authentication results\n\n"
        "{% include [User token](_assets/user-token.md) %}\n\n"
        "{% include [Lifecycle](_assets/user-token-lifecycle.md) %}\n"
    )
    pair_runs = [
        _translated_run(caching, caching_text),
        _translated_run(user_token, "```mermaid\ngraph LR\n```\n"),
        _translated_run(lifecycle, "```mermaid\ngraph TD\n```\n"),
        *[_translated_run(path, f"# Page {index}\n") for index, path in enumerate(ordinary_pages, 1)],
    ]
    pending_toc = "items:\n" + "".join(
        f"- name: Page\n  href: {Path(path).name}\n"
        for path in [caching, *ordinary_pages]
    )
    result = PRTranslationResult(
        pair_results=pair_runs,
        navigation_results=[
            NavigationRunResult(
                ru_path=f"{security.replace('/en/', '/ru/', 1)}/toc_p.yaml",
                en_path=f"{security}/toc_p.yaml",
                kind="toc",
                target_text=pending_toc,
                verdict="ok",
            )
        ],
    )

    orphans = apply_orphan_toc_page_checks(
        result,
        repo_path=repo,
        docs_root="ydb/docs",
        baseline_ref="HEAD",
    )

    assert len(pair_runs) == 8
    assert orphans == []
    assert all(run.file_result and run.file_result.verdict == "ok" for run in pair_runs)

    pair_runs[0].file_result.final_text = "# Caching authentication results\n"
    assert apply_orphan_toc_page_checks(
        result,
        repo_path=repo,
        docs_root="ydb/docs",
        baseline_ref="HEAD",
    ) == [lifecycle, user_token]


def test_nested_yfm_include_chain_is_transitively_reachable(tmp_path: Path):
    repo = _init_repo(tmp_path)
    section = "ydb/docs/en/core/section"
    landing = f"{section}/index.md"
    first = f"{section}/fragments/first.md"
    second = f"{section}/fragments/nested/second.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Section\n  href: section/index.md\n",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    result = PRTranslationResult(
        pair_results=[
            _translated_run(
                landing,
                "# Section\n\n{% include [First](fragments/first.md) %}\n",
            ),
            _translated_run(
                first,
                "{% include [Second](nested/second.md) %}\n",
            ),
            _translated_run(second, "Nested fragment.\n"),
        ]
    )

    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == []


def test_yfm_include_outside_includes_directory_is_reachable(tmp_path: Path):
    repo = _init_repo(tmp_path)
    section = "ydb/docs/en/core/section"
    landing = f"{section}/index.md"
    fragment = f"{section}/partials/shared.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Section\n  href: section/index.md\n",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    result = PRTranslationResult(
        pair_results=[
            _translated_run(
                landing,
                "# Section\n\n{% include [Shared](partials/shared.md) %}\n",
            ),
            _translated_run(fragment, "Shared fragment.\n"),
        ]
    )

    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == []


def test_pending_file_result_final_text_overlays_frozen_file_before_write(
    tmp_path: Path,
):
    repo = _init_repo(tmp_path)
    section = "ydb/docs/en/core/section"
    landing = f"{section}/index.md"
    fragment = f"{section}/_assets/pending.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Section\n  href: section/index.md\n",
    )
    _write(repo, landing, "# Frozen page without the new include\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    landing_run = _translated_run(
        landing,
        "# Candidate\n\n{% include [Pending](_assets/pending.md) %}\n",
    )
    landing_run.target_text = "# Stale run target without include\n"
    result = PRTranslationResult(
        pair_results=[landing_run, _translated_run(fragment, "Pending fragment.\n")]
    )

    assert not Path(repo, fragment).exists()
    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == []


def test_pending_empty_final_text_removes_frozen_include_authoritatively(
    tmp_path: Path,
):
    repo = _init_repo(tmp_path)
    section = "ydb/docs/en/core/section"
    landing = f"{section}/index.md"
    fragment = f"{section}/_assets/removed.md"
    include_line = "{% include [Removed](_assets/removed.md) %}\n"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Section\n  href: section/index.md\n",
    )
    _write(repo, landing, include_line)
    _write(repo, fragment, "Frozen fragment.\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    landing_run = _translated_run(landing, "")
    landing_run.target_text = include_line
    result = PRTranslationResult(
        pair_results=[landing_run, _translated_run(fragment, "Candidate fragment.\n")]
    )

    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == [fragment]


def test_orphan_referrer_does_not_make_its_include_reachable(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _write(repo, "ydb/docs/en/core/toc_p.yaml", "items: []\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    parent = "ydb/docs/en/core/orphan-parent.md"
    child = "ydb/docs/en/core/fragments/orphan-child.md"
    result = PRTranslationResult(
        pair_results=[
            _translated_run(
                parent,
                "{% include [Child](fragments/orphan-child.md) %}\n",
            ),
            _translated_run(child, "Child.\n"),
        ]
    )

    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == [child, parent]


def test_missing_yfm_include_target_keeps_existing_typed_blocker(tmp_path: Path):
    repo = _init_repo(tmp_path)
    landing = "ydb/docs/en/core/section/index.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Section\n  href: section/index.md\n",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    landing_run = _translated_run(
        landing,
        "# Section\n\n{% include [Missing](_assets/missing.md) %}\n",
    )
    result = PRTranslationResult(pair_results=[landing_run])

    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == []
    apply_include_target_checks(result, repo_path=repo)

    assert landing_run.file_result is not None
    assert landing_run.file_result.verdict == "blocked"
    assert any(
        message.startswith("include_target:")
        for message in landing_run.file_result.heuristic_blocking
    )


def test_yfm_include_cycles_terminate_without_reaching_orphan_cycle(tmp_path: Path):
    repo = _init_repo(tmp_path)
    section = "ydb/docs/en/core/section"
    landing = f"{section}/index.md"
    reachable_fragment = f"{section}/fragments/reachable.md"
    orphan_a = f"{section}/orphan-a.md"
    orphan_b = f"{section}/orphan-b.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Section\n  href: section/index.md\n",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    result = PRTranslationResult(
        pair_results=[
            _translated_run(
                landing,
                "{% include [Reachable](fragments/reachable.md) %}\n",
            ),
            _translated_run(
                reachable_fragment,
                "{% include [Back](../index.md) %}\n",
            ),
            _translated_run(orphan_a, "{% include [B](orphan-b.md) %}\n"),
            _translated_run(orphan_b, "{% include [A](orphan-a.md) %}\n"),
        ]
    )

    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == [orphan_a, orphan_b]


def test_normal_markdown_link_to_assets_page_is_not_include_evidence(tmp_path: Path):
    repo = _init_repo(tmp_path)
    landing = "ydb/docs/en/core/section/index.md"
    fragment = "ydb/docs/en/core/section/_assets/linked.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Section\n  href: section/index.md\n",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    result = PRTranslationResult(
        pair_results=[
            _translated_run(landing, "[Linked fragment](_assets/linked.md)\n"),
            _translated_run(fragment, "Linked fragment.\n"),
        ]
    )

    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == [fragment]


def test_protected_yfm_include_examples_are_not_include_evidence(tmp_path: Path):
    repo = _init_repo(tmp_path)
    landing = "ydb/docs/en/core/section/index.md"
    fenced = "ydb/docs/en/core/section/_assets/fenced.md"
    commented = "ydb/docs/en/core/section/_assets/commented.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Section\n  href: section/index.md\n",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    result = PRTranslationResult(
        pair_results=[
            _translated_run(
                landing,
                "```text\n{% include [Example](_assets/fenced.md) %}\n```\n\n"
                "<!--\n{% include [Comment](_assets/commented.md) %}\n-->\n",
            ),
            _translated_run(fenced, "Fenced example target.\n"),
            _translated_run(commented, "Commented example target.\n"),
        ]
    )

    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == [commented, fenced]


def test_tab_indented_yfm_include_example_is_not_include_evidence(tmp_path: Path):
    repo = _init_repo(tmp_path)
    landing = "ydb/docs/en/core/section/index.md"
    target = "ydb/docs/en/core/section/_assets/tab.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Section\n  href: section/index.md\n",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    result = PRTranslationResult(
        pair_results=[
            _translated_run(
                landing,
                "# Page\n\n\t{% include [Example](_assets/tab.md) %}\n",
            ),
            _translated_run(target, "Tab-indented example target.\n"),
        ]
    )

    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == [target]


def test_front_matter_scalar_yfm_include_example_is_not_include_evidence(
    tmp_path: Path,
):
    repo = _init_repo(tmp_path)
    landing = "ydb/docs/en/core/section/index.md"
    target = "ydb/docs/en/core/section/_assets/front.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Section\n  href: section/index.md\n",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    result = PRTranslationResult(
        pair_results=[
            _translated_run(
                landing,
                "---\nexample: |\n  {% include [Example](_assets/front.md) %}\n"
                "---\n# Page\n",
            ),
            _translated_run(target, "Front-matter example target.\n"),
        ]
    )

    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == [target]


def test_frozen_reader_does_not_use_dirty_worktree_include_target(tmp_path: Path):
    repo = _init_repo(tmp_path)
    landing = "ydb/docs/en/core/section/index.md"
    dirty_only = "ydb/docs/en/core/section/_assets/dirty-only.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Section\n  href: section/index.md\n",
    )
    _write(repo, landing, "{% include [Dirty](_assets/dirty-only.md) %}\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    _write(repo, dirty_only, "Only in the dirty worktree.\n")

    assert check_orphan_translated_pages(
        {dirty_only},
        repo_path=repo,
        baseline_ref="HEAD",
        pending_md_texts={},
    ) == {
        dirty_only: [
            "orphan_toc_page: "
            f"translated EN page `{dirty_only}` is not linked from any EN toc "
            "(reachable from `ydb/docs/en/core/toc_p.yaml` via href/include.path)"
        ]
    }


@pytest.mark.parametrize("unavailable_kind", ["deleted", "error", "no_file_result"])
def test_unavailable_pending_referrer_cannot_revive_from_frozen_baseline(
    tmp_path: Path,
    unavailable_kind: str,
):
    repo = _init_repo(tmp_path)
    landing = "ydb/docs/en/core/section/index.md"
    fragment = "ydb/docs/en/core/section/_assets/child.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Section\n  href: section/index.md\n",
    )
    _write(repo, landing, "{% include [Child](_assets/child.md) %}\n")
    _write(repo, fragment, "Frozen child.\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    unavailable = PairRunResult(
        plan=_pair_plan(landing),
        deleted=unavailable_kind == "deleted",
        error="failed" if unavailable_kind == "error" else None,
    )
    result = PRTranslationResult(
        pair_results=[unavailable, _translated_run(fragment, "Candidate child.\n")]
    )

    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == [fragment]


def test_skipped_referrer_may_use_unchanged_frozen_file(tmp_path: Path):
    repo = _init_repo(tmp_path)
    landing = "ydb/docs/en/core/section/index.md"
    fragment = "ydb/docs/en/core/section/_assets/child.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Section\n  href: section/index.md\n",
    )
    _write(repo, landing, "{% include [Child](_assets/child.md) %}\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(plan=_pair_plan(landing), skipped=True),
            _translated_run(fragment, "Candidate child.\n"),
        ]
    )

    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == []


def test_include_proof_rejects_locale_escape_before_path_normalization(
    tmp_path: Path,
):
    repo = _init_repo(tmp_path)
    landing = "ydb/docs/en/core/guide/page.md"
    fragment = "ydb/docs/en/core/_assets/child.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Guide\n  href: guide/page.md\n",
    )
    _write(repo, fragment, "Existing child.\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)

    escaping = (
        "{% include [Child](../../../ru/../en/core/_assets/child.md) %}\n"
    )
    assert fragment in check_orphan_translated_pages(
        {fragment},
        repo_path=repo,
        baseline_ref="HEAD",
        pending_md_texts={landing: escaping},
    )

    within_locale = "{% include [Child](../../core/_assets/child.md) %}\n"
    assert check_orphan_translated_pages(
        {fragment},
        repo_path=repo,
        baseline_ref="HEAD",
        pending_md_texts={landing: within_locale},
    ) == {}


def test_absent_or_failed_pending_toc_does_not_seed_disconnected_baseline(
    tmp_path: Path,
):
    repo = _init_repo(tmp_path)
    disconnected_toc = "ydb/docs/en/core/section/toc_p.yaml"
    connected_toc = "ydb/docs/en/core/connected/toc_p.yaml"
    absent_target = "ydb/docs/en/core/section/absent-nav-result.md"
    connected_target = "ydb/docs/en/core/connected/existing-nav-result.md"
    failed_target = "ydb/docs/en/core/section/failed-nav-result.md"
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Connected\n"
        "  include: { mode: link, path: connected/toc_p.yaml }\n",
    )
    _write(
        repo,
        disconnected_toc,
        "items:\n- name: Absent\n  href: absent-nav-result.md\n",
    )
    _write(
        repo,
        connected_toc,
        "items:\n- name: Existing\n  href: existing-nav-result.md\n",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)
    absent_nav = NavigationRunResult(
        ru_path=disconnected_toc.replace("/en/", "/ru/", 1),
        en_path=disconnected_toc,
        kind="toc",
        target_text=None,
        verdict="ok",
    )
    connected_nav = NavigationRunResult(
        ru_path=connected_toc.replace("/en/", "/ru/", 1),
        en_path=connected_toc,
        kind="toc",
        target_text=None,
        verdict="ok",
    )
    failed_nav = NavigationRunResult(
        ru_path="ydb/docs/ru/core/failed/toc_p.yaml",
        en_path="ydb/docs/en/core/failed/toc_p.yaml",
        kind="toc",
        target_text="items:\n- name: Failed\n  href: ../section/failed-nav-result.md\n",
        verdict="blocked",
        error="navigation failed",
    )
    result = PRTranslationResult(
        pair_results=[
            _translated_run(absent_target, "Absent nav result target.\n"),
            _translated_run(connected_target, "Connected nav result target.\n"),
            _translated_run(failed_target, "Failed nav result target.\n"),
        ],
        navigation_results=[absent_nav, connected_nav, failed_nav],
    )

    assert apply_orphan_toc_page_checks(
        result, repo_path=repo, baseline_ref="HEAD"
    ) == [absent_target, failed_target]


def test_find_locale_pages_missing_from_toc_en_and_ru(tmp_path: Path):
    """Repo-wide audit finds orphans in both locale trees."""
    repo = _init_repo(tmp_path)
    _write(
        repo,
        "ydb/docs/en/core/toc_p.yaml",
        "items:\n- name: Index\n  href: index.md\n",
    )
    _write(repo, "ydb/docs/en/core/index.md", "# EN\n")
    _write(repo, "ydb/docs/en/core/orphan-en.md", "# orphan EN\n")
    _write(
        repo,
        "ydb/docs/ru/core/toc_p.yaml",
        "items:\n- name: Index\n  href: index.md\n",
    )
    _write(repo, "ydb/docs/ru/core/index.md", "# RU\n")
    _write(repo, "ydb/docs/ru/core/orphan-ru.md", "# orphan RU\n")
    _write(repo, "ydb/docs/en/core/_includes/x.md", "# include\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "docs"], cwd=repo, check=True)

    en = find_locale_pages_missing_from_toc(repo, locale="en")
    ru = find_locale_pages_missing_from_toc(repo, locale="ru")
    assert en == ["ydb/docs/en/core/orphan-en.md"]
    assert ru == ["ydb/docs/ru/core/orphan-ru.md"]

    both = find_pages_missing_from_toc(repo, locales=("en", "ru"))
    assert both["en"] == en
    assert both["ru"] == ru
