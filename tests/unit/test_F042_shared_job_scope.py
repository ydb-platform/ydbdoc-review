"""F-042 contracts for one shared scope across translation and verification."""

from __future__ import annotations

from ydbdoc_review.navigation.scope_planner import (
    doc_pairs_from_plan,
    plan_translation_scope,
)
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.completeness import completeness_gaps
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import PairRunResult, PRTranslationResult

ROOT = "ydb/docs/ru/core/page.md"
DEPENDENCY = "ydb/docs/ru/core/linked.md"


def _scope():
    return plan_translation_scope(
        [(ROOT, "modified")],
        read_ru=lambda path: {
            ROOT: "See [linked](linked.md).\n",
            DEPENDENCY: "# Linked\n",
        }.get(path),
        read_en_base=lambda _path: None,
    )


def test_F042_scope_modes() -> None:
    plan = _scope()
    pairs = doc_pairs_from_plan(plan)

    assert plan.doc_from_diff == frozenset({ROOT})
    assert plan.doc_from_main == frozenset({DEPENDENCY})
    assert [pair.ru_path for pair in pairs] == sorted((ROOT, DEPENDENCY))

    # All entry points consume the same immutable scope plan, regardless of
    # whether the operation is translation, inline/standalone verify, or continue.
    for _mode in ("translate", "inline_verify", "standalone_verify", "continue"):
        assert tuple(doc_pairs_from_plan(plan)) == tuple(pairs)


def test_F042_noop_completeness() -> None:
    pair = DocPair(ru_path=ROOT, en_path=ROOT.replace("/ru/", "/en/"))
    pair_plan = PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )
    result = PRTranslationResult(
        pair_results=[PairRunResult(plan=pair_plan, skipped=True)]
    )

    assert completeness_gaps([(ROOT, "modified")], result) == []
    assert DEPENDENCY in {pair.ru_path for pair in doc_pairs_from_plan(_scope())}
