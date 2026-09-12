"""Acceptance: new EN from TOC-reachable RU updates EN TOC in same transaction (§11.1 / P5)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.github.git_ops import git_commit_paths
from ydbdoc_review.github.workflow import (
    DocJobResult,
    _apply_results_to_disk,
    _publication_withheld,
    job_requires_nonzero_exit,
)
from ydbdoc_review.navigation.scope_planner import TranslationScopePlan
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.navigation_merge import merge_navigation_pair
from ydbdoc_review.pipeline.pairs import DocPair, NavigationPair
from ydbdoc_review.pipeline.publication import refresh_publication_impact
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.validation.toc_targets import apply_orphan_toc_page_checks

RU_TOC = "ydb/docs/ru/core/demo/toc_i.yaml"
EN_TOC = "ydb/docs/en/core/demo/toc_i.yaml"
RU_NEW = "ydb/docs/ru/core/demo/new.md"
EN_NEW = "ydb/docs/en/core/demo/new.md"
RU_ORPHAN = "ydb/docs/ru/core/demo/orphan.md"
EN_ORPHAN = "ydb/docs/en/core/demo/orphan.md"
RU_REDIRECTS = "ydb/docs/ru/redirects.yaml"
EN_REDIRECTS = "ydb/docs/en/redirects.yaml"

RU_TOC_WITH_NEW = dedent("""
    items:
    - { name: Обзор, href: index.md }
    - { name: Новая, href: new.md }
""").strip()

RU_TOC_INDEX_ONLY = dedent("""
    items:
    - { name: Обзор, href: index.md }
""").strip()

EN_TOC_INDEX_ONLY = dedent("""
    items:
     - { name: Overview, href: index.md }
""").strip()

EN_NEW_BODY = "# New page\n\nTranslated content.\n"
EN_ORPHAN_BODY = "# Orphan page\n\nTranslated but not in TOC.\n"


def _cfg():
    return load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})


def _pair_result(en_path: str, target_text: str) -> PairRunResult:
    pair = DocPair(
        ru_path=en_path.replace("/en/", "/ru/"),
        en_path=en_path,
        ru_changed=True,
    )
    plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
        summary="P5 §11.1",
    )
    file_result = FileTranslationResult(
        file_path=en_path,
        final_text=target_text,
        segments_count=1,
        verdict="ok",
        prompt_version="v1",
    )
    return PairRunResult(plan=plan, target_text=target_text, file_result=file_result)


def _write(repo: Path, relative_path: str, text: str) -> None:
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _init_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)
    for relative_path, text in files.items():
        _write(repo, relative_path, text)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=P5 Test",
            "-c",
            "user.email=p5@example.com",
            "commit",
            "-m",
            "base",
        ],
        cwd=repo,
        check=True,
    )
    return repo


def _commit_candidate(repo: Path, result: PRTranslationResult) -> tuple[str, ...]:
    touched = _apply_results_to_disk(str(repo), result, dry_run=False)
    assert git_commit_paths(
        str(repo),
        touched.written,
        "P5 candidate",
        "P5 Test",
        "p5@example.com",
        deleted_paths=touched.deleted,
    )
    committed_paths = subprocess.check_output(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        cwd=repo,
        text=True,
    ).splitlines()
    return tuple(committed_paths)


def _scope_plan_for_new() -> TranslationScopePlan:
    return TranslationScopePlan(
        doc_ru_paths=frozenset({RU_NEW}),
        doc_from_diff=frozenset({RU_NEW}),
        doc_from_main=frozenset(),
        nav_ru_paths=frozenset({RU_TOC}),
        nav_from_diff=frozenset(),
        nav_from_main=frozenset({RU_TOC}),
    )


def test_toc_reachable_new_en_lands_in_en_toc_same_transaction(tmp_path: Path):
    """§11.1 / §14: validated new EN and EN TOC share one candidate commit."""
    repo = _init_repo(
        tmp_path,
        {
            EN_TOC: EN_TOC_INDEX_ONLY,
            "ydb/docs/en/core/demo/index.md": "# Overview\n",
        },
    )
    client = MagicMock()
    glossary = load_glossary()
    pair = NavigationPair(ru_path=RU_TOC, en_path=EN_TOC, ru_changed=True, supplement_only=True)

    with (
        patch(
            "ydbdoc_review.pipeline.navigation_merge.read_text",
            return_value=RU_TOC_WITH_NEW,
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._read_navigation_baselines",
            # RU already lists new.md; EN omits it → planned extras pull href in.
            return_value=(RU_TOC_WITH_NEW, EN_TOC_INDEX_ONLY),
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._translate_menu_labels",
            return_value={"Новая": "New"},
        ),
    ):
        nav = merge_navigation_pair(
            pair,
            repo_path=str(repo),
            merge_base_with="origin/main",
            client=client,
            glossary=glossary,
            config=_cfg(),
            scope_plan=_scope_plan_for_new(),
            active_doc_ru_paths=frozenset({RU_NEW}),
        )

    assert nav.error is None
    assert nav.target_text is not None
    assert "new.md" in nav.target_text
    assert "New" in nav.target_text

    result = PRTranslationResult(
        pair_results=[_pair_result(EN_NEW, EN_NEW_BODY)],
        navigation_results=[nav],
    )
    assert apply_orphan_toc_page_checks(result, repo_path=str(repo)) == []
    assert refresh_publication_impact(result) == PublicationImpact.PUBLISH_NORMAL

    committed_paths = _commit_candidate(repo, result)
    assert set(committed_paths) == {EN_NEW, EN_TOC}
    assert subprocess.check_output(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo, text=True
    ).strip() == "2"
    assert (repo / EN_NEW).read_text(encoding="utf-8") == EN_NEW_BODY
    toc_on_disk = (repo / EN_TOC).read_text(encoding="utf-8")
    assert "new.md" in toc_on_disk
    assert "New" in toc_on_disk


def test_orphan_en_not_forced_into_en_toc(tmp_path: Path):
    """§11.1: an orphan candidate is withheld, not partially published as success."""
    repo = _init_repo(
        tmp_path,
        {
            EN_TOC: EN_TOC_INDEX_ONLY,
            "ydb/docs/en/core/demo/index.md": "# Overview\n",
        },
    )
    client = MagicMock()
    glossary = load_glossary()
    pair = NavigationPair(ru_path=RU_TOC, en_path=EN_TOC, ru_changed=True, supplement_only=True)
    scope = TranslationScopePlan(
        doc_ru_paths=frozenset({RU_ORPHAN}),
        doc_from_diff=frozenset({RU_ORPHAN}),
        doc_from_main=frozenset(),
        nav_ru_paths=frozenset({RU_TOC}),
        nav_from_diff=frozenset(),
        nav_from_main=frozenset({RU_TOC}),
    )

    with (
        patch(
            "ydbdoc_review.pipeline.navigation_merge.read_text",
            return_value=RU_TOC_INDEX_ONLY,
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._read_navigation_baselines",
            return_value=(RU_TOC_INDEX_ONLY, EN_TOC_INDEX_ONLY),
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._translate_menu_labels",
            return_value={},
        ),
    ):
        nav = merge_navigation_pair(
            pair,
            repo_path=str(repo),
            merge_base_with="origin/main",
            client=client,
            glossary=glossary,
            config=_cfg(),
            scope_plan=scope,
            active_doc_ru_paths=frozenset({RU_ORPHAN}),
        )

    # No toc delta vs EN main → merge is a no-op (target_text None).
    assert nav.error is None
    assert nav.target_text is None or "orphan.md" not in (nav.target_text or "")

    result = PRTranslationResult(
        pair_results=[_pair_result(EN_ORPHAN, EN_ORPHAN_BODY)],
        navigation_results=[nav] if nav.target_text is not None else [],
    )
    orphan_paths = apply_orphan_toc_page_checks(result, repo_path=str(repo))
    result.completeness_gaps = orphan_paths

    assert orphan_paths == [EN_ORPHAN]
    assert refresh_publication_impact(result) == PublicationImpact.WITHHOLD_INCOMPLETE
    assert _publication_withheld(result)
    assert job_requires_nonzero_exit(
        DocJobResult(mode="doc_translate", pr_number=5, pr_result=result)
    )
    assert not (repo / EN_ORPHAN).exists()
    assert (repo / EN_TOC).read_text(encoding="utf-8") == EN_TOC_INDEX_ONLY
    assert subprocess.check_output(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo, text=True
    ).strip() == "1"


def test_redirect_update_shares_transaction_with_new_en(tmp_path: Path):
    """§11.1: page, TOC, and redirects join one validated candidate commit."""
    ru_base = dedent("""
        - from: /docs/old
          to: /docs/index
    """).strip()
    ru_pr = dedent("""
        - from: /docs/old
          to: /docs/index
        - from: /docs/legacy-new
          to: /docs/new
    """).strip()
    en_main = dedent("""
        - from: /docs/old
          to: /docs/index
    """).strip()

    repo = _init_repo(
        tmp_path,
        {
            EN_TOC: EN_TOC_INDEX_ONLY,
            EN_REDIRECTS: en_main,
            "ydb/docs/en/core/demo/index.md": "# Overview\n",
        },
    )
    client = MagicMock()
    glossary = load_glossary()
    pair = NavigationPair(ru_path=RU_REDIRECTS, en_path=EN_REDIRECTS, ru_changed=True)

    with (
        patch(
            "ydbdoc_review.pipeline.navigation_merge.read_text",
            return_value=ru_pr,
        ),
        patch(
            "ydbdoc_review.pipeline.navigation_merge._read_navigation_baselines",
            return_value=(ru_base, en_main),
        ),
    ):
        nav = merge_navigation_pair(
            pair,
            repo_path=str(repo),
            merge_base_with="origin/main",
            client=client,
            glossary=glossary,
            config=_cfg(),
        )

    assert nav.error is None
    assert nav.target_text is not None
    assert "/docs/legacy-new" in nav.target_text

    # Pair the redirect write with a TOC-reachable new page in one overlay.
    toc_nav = NavigationRunResult(
        ru_path=RU_TOC,
        en_path=EN_TOC,
        kind="toc",
        target_text=EN_TOC_INDEX_ONLY + "\n - { name: New, href: new.md }\n",
        verdict="ok",
    )
    result = PRTranslationResult(
        pair_results=[_pair_result(EN_NEW, EN_NEW_BODY)],
        navigation_results=[toc_nav, nav],
    )
    assert apply_orphan_toc_page_checks(result, repo_path=str(repo)) == []
    assert refresh_publication_impact(result) == PublicationImpact.PUBLISH_NORMAL

    committed_paths = _commit_candidate(repo, result)
    assert set(committed_paths) == {EN_NEW, EN_TOC, EN_REDIRECTS}
    assert subprocess.check_output(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo, text=True
    ).strip() == "2"
    assert "new.md" in (repo / EN_TOC).read_text(encoding="utf-8")
    assert "/docs/legacy-new" in (repo / EN_REDIRECTS).read_text(encoding="utf-8")
