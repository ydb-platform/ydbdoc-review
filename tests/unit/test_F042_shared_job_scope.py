"""F-042 contracts for one shared source-job scope."""

from __future__ import annotations

from ydbdoc_review.github.workflow import _translation_checkpoint_scope
from ydbdoc_review.navigation.scope_planner import (
    TranslationScopePlan,
    doc_pairs_from_plan,
    navigation_pairs_from_plan,
    synthetic_changes_from_plan,
)
from ydbdoc_review.pipeline.analyze import PairContent, plan_from_analyze
from ydbdoc_review.pipeline.completeness import (
    completeness_gaps,
    translation_pr_scope_gaps,
)
from ydbdoc_review.pipeline.pairs import (
    DocPair,
    NavigationPair,
    filter_translation_pr_verify_scope,
)
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult
from ydbdoc_review.translation.schemas import AnalyzePairResult


def _scope() -> TranslationScopePlan:
    return TranslationScopePlan(
        doc_ru_paths=frozenset(
            {
                "ydb/docs/ru/a.md",
                "ydb/docs/ru/_includes/shared.md",
            }
        ),
        doc_from_diff=frozenset({"ydb/docs/ru/a.md"}),
        doc_from_main=frozenset({"ydb/docs/ru/_includes/shared.md"}),
        nav_ru_paths=frozenset({"ydb/docs/ru/toc_p.yaml"}),
        nav_from_diff=frozenset(),
        nav_from_main=frozenset({"ydb/docs/ru/toc_p.yaml"}),
    )


def test_F042_scope_modes():
    """Translate, both verify modes, and continue use one scope manifest."""
    scope = _scope()
    expected_pairs = doc_pairs_from_plan(scope)
    expected_nav = navigation_pairs_from_plan(scope)
    translation_changes = [
        *[(path, "modified") for path in scope.doc_from_diff | scope.nav_from_diff],
        *synthetic_changes_from_plan(scope),
    ]
    allowed_en = frozenset(pair.en_path for pair in expected_pairs)
    allowed_nav = frozenset(nav.en_path for nav in expected_nav)

    inline_pairs, inline_nav = filter_translation_pr_verify_scope(
        expected_pairs,
        expected_nav,
        [(path.replace("/ru/", "/en/"), kind) for path, kind in translation_changes],
        allowed_en_paths=allowed_en,
        allowed_nav_en_paths=allowed_nav,
    )
    standalone_pairs, standalone_nav = filter_translation_pr_verify_scope(
        expected_pairs,
        expected_nav,
        [(path.replace("/ru/", "/en/"), kind) for path, kind in translation_changes],
        allowed_en_paths=allowed_en,
        allowed_nav_en_paths=allowed_nav,
    )

    assert {(p.ru_path, p.en_path) for p in expected_pairs} == {
        (p.ru_path, p.en_path) for p in inline_pairs
    }
    assert {(p.ru_path, p.en_path) for p in expected_pairs} == {
        (p.ru_path, p.en_path) for p in standalone_pairs
    }
    expected_committed_nav = [p for p in expected_nav if not p.supplement_only]
    assert [p.en_path for p in expected_committed_nav] == [
        p.en_path for p in inline_nav
    ]
    assert [p.en_path for p in expected_committed_nav] == [
        p.en_path for p in standalone_nav
    ]

    checkpoint_scope = _translation_checkpoint_scope(scope)
    assert set(checkpoint_scope) == {path for path in scope.all_ru_paths}
    assert checkpoint_scope == {
        path: tuple(
            name
            for name, paths in (
                ("doc_from_diff", scope.doc_from_diff),
                ("doc_from_main", scope.doc_from_main),
                ("nav_from_diff", scope.nav_from_diff),
                ("nav_from_main", scope.nav_from_main),
                ("doc_deleted", scope.doc_deleted),
            )
            if path in paths
        )
        for path in sorted(scope.all_ru_paths | scope.doc_deleted)
    }


def test_F042_noop_completeness():
    """Analyze no-op still satisfies itself but cannot hide a missing dependency."""
    pair = DocPair(
        ru_path="ydb/docs/ru/a.md",
        en_path="ydb/docs/en/a.md",
        ru_changed=True,
    )
    dependency = DocPair(
        ru_path="ydb/docs/ru/_includes/shared.md",
        en_path="ydb/docs/en/_includes/shared.md",
        ru_changed=True,
    )
    analyzed = plan_from_analyze(
        PairContent(pair=pair, ru_text="RU", en_text="EN"),
        AnalyzePairResult(
            ru_path=pair.ru_path,
            en_path=pair.en_path,
            ru_present=True,
            en_present=True,
            semantically_aligned=True,
            needs_generation_for=None,
            summary="no_translation_needed",
        ),
    )
    result = PRTranslationResult(
        pair_results=[PairRunResult(plan=analyzed, skipped=True)],
    )

    assert analyzed.action == "critic_only"
    assert completeness_gaps([(pair.ru_path, "modified")], result) == []
    assert translation_pr_scope_gaps(
        [pair, dependency],
        [
            NavigationPair(
                ru_path="ydb/docs/ru/toc_p.yaml",
                en_path="ydb/docs/en/toc_p.yaml",
                ru_changed=True,
            )
        ],
        [(pair.en_path, "modified")],
    ) == ["ydb/docs/en/_includes/shared.md", "ydb/docs/en/toc_p.yaml"]
