"""F-055: a defective candidate is distinct from no translation."""

from __future__ import annotations

from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.publication import refresh_publication_impact
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)


def _plan() -> PairPlan:
    pair = DocPair(
        ru_path="ydb/docs/ru/f055.md",
        en_path="ydb/docs/en/f055.md",
        ru_changed=True,
    )
    return PairPlan(
        pair=pair,
        action="translate_to_en",
        source_path=pair.ru_path,
        target_path=pair.en_path,
        source_lang="ru",
        target_lang="en",
    )


def test_F055_bad_candidate() -> None:
    plan = _plan()
    candidate = FileTranslationResult(
        file_path=plan.target_path,
        final_text="Candidate with an unresolved marker ⟦C1⟧\n",
        segments_count=1,
        verdict="warnings",
        prompt_version="test",
        heuristic_blocking=["unrestored_placeholder: C1"],
    )
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                target_text=candidate.final_text,
                source_text="Исходный текст\n",
                file_result=candidate,
            )
        ]
    )

    assert refresh_publication_impact(result) == PublicationImpact.PUBLISH_RED
    assert result.pair_results[0].target_text
    assert result.pair_results[0].error is None


def test_F055_no_candidate() -> None:
    plan = _plan()
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=plan,
                error="translation_failed: primary and fallback exhausted",
                target_text=None,
                source_text="Исходный текст\n",
            )
        ]
    )

    assert refresh_publication_impact(result) == PublicationImpact.WITHHOLD_INCOMPLETE
    assert result.pair_results[0].target_text is None
    assert result.pair_results[0].source_text != result.pair_results[0].target_text
