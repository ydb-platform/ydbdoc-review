"""R-GL-2 / §6.242: merged PR tip redirects vs merge-era tombstone paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.navigation.redirects import (
    follow_redirect_repo_md_path,
    redirect_source_repo_md_paths,
    should_skip_redirect_tombstone_en,
)
from ydbdoc_review.navigation.scope_planner import (
    doc_pairs_from_plan,
    plan_translation_scope,
)
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.completeness import completeness_gaps
from ydbdoc_review.pipeline.orchestrator import run_pr_translation
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.validation.toc_targets import apply_orphan_toc_page_checks


MANUAL_RU = "ydb/docs/ru/core/devops/deployment-options/manual/node-authorization.md"
MANUAL_EN = "ydb/docs/en/core/devops/deployment-options/manual/node-authorization.md"
CONCEPTS_RU = "ydb/docs/ru/core/devops/concepts/node-authorization.md"
CONCEPTS_EN = "ydb/docs/en/core/devops/concepts/node-authorization.md"

TIP_REDIRECTS = (
    "common:\n"
    "  - from: /devops/deployment-options/manual/node-authorization.md\n"
    "    to: /devops/concepts/node-authorization.md\n"
)
MERGE_REDIRECTS = "common: []\n"


def test_follow_redirect_repo_md_path_maps_manual_to_concepts():
    assert follow_redirect_repo_md_path(MANUAL_RU, TIP_REDIRECTS) == CONCEPTS_RU
    assert follow_redirect_repo_md_path(CONCEPTS_RU, TIP_REDIRECTS) == CONCEPTS_RU


def test_tip_tombstone_skip_uses_tip_redirects_not_merge_era():
    """Merge-era redirects lack the tombstone; tip has it — skip must use tip."""
    merge_sources = redirect_source_repo_md_paths(MERGE_REDIRECTS, locale="en")
    tip_sources = redirect_source_repo_md_paths(TIP_REDIRECTS, locale="en")
    assert MANUAL_EN not in merge_sources
    assert MANUAL_EN in tip_sources
    assert not should_skip_redirect_tombstone_en(
        MANUAL_EN, redirect_source_en_paths=merge_sources
    )
    assert should_skip_redirect_tombstone_en(
        MANUAL_EN, redirect_source_en_paths=tip_sources
    )


def test_r_gl_2_merge_era_manual_in_scope_tip_tombstone_skips_en_write(tmp_path: Path):
    """Acceptance: merge-era manual + tip redirect → no EN write / orphan at manual."""
    tip_sources = redirect_source_repo_md_paths(TIP_REDIRECTS, locale="en")

    merge_ru = {
        MANUAL_RU: "# Авторизация узлов\n\n## Включение режима…\n",
        CONCEPTS_RU: "# Авторизация узлов\n\n## Включение режима…\n",
        "ydb/docs/redirects.yaml": MERGE_REDIRECTS,
    }
    tip_en = {
        CONCEPTS_EN: "# Node authorization\n\n## Enabling…\n",
        "ydb/docs/redirects.yaml": TIP_REDIRECTS,
    }
    plan = plan_translation_scope(
        [(MANUAL_RU, "modified")],
        read_ru=merge_ru.get,
        read_en_base=tip_en.get,
        read_ru_base=lambda p: None,
    )
    assert MANUAL_RU in plan.doc_from_diff
    pairs = doc_pairs_from_plan(plan)
    manual_pair = next(p for p in pairs if p.en_path == MANUAL_EN)
    contents = [
        PairContent(
            pair=manual_pair,
            ru_text=merge_ru[MANUAL_RU],
            en_text=None,
        )
    ]

    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    result = run_pr_translation(
        contents,
        MagicMock(),
        load_glossary(),
        use_analyze_llm=False,
        config=cfg,
        redirect_source_en_paths=tip_sources,
        en_toc_reachable=frozenset({CONCEPTS_EN}),
    )
    assert result.translated_count == 0
    assert len(result.pair_results) == 1
    run = result.pair_results[0]
    assert run.skipped
    assert run.plan.action == "skip"
    assert "redirect tombstone" in run.plan.summary
    assert run.target_text is None

    assert completeness_gaps([(MANUAL_RU, "modified")], result) == []

    orphans = apply_orphan_toc_page_checks(
        result,
        repo_path=str(tmp_path),
        docs_root="ydb/docs",
        exempt_en_paths=tip_sources,
    )
    assert orphans == []
    assert MANUAL_EN not in {
        r.plan.target_path
        for r in result.pair_results
        if r.target_text is not None
    }
