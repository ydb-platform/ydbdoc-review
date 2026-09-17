"""F-090: complete textual and mechanical results are publishable."""

from __future__ import annotations

import pytest

from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.publication import evaluate_publication_impact
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    FinalTreeBlocker,
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)


def _plan() -> PairPlan:
    pair = DocPair(
        ru_path="ydb/docs/ru/f090.md",
        en_path="ydb/docs/en/f090.md",
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


@pytest.mark.parametrize("mode", ["translate", "continue"])
def test_F090_modes_diff(mode: str) -> None:
    """Both modes publish a complete textual or mechanical candidate as RED."""
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=_plan(),
                source_text="Исходный текст.\n",
                target_text="Complete candidate.\n",
                file_result=FileTranslationResult(
                    file_path="ydb/docs/en/f090.md",
                    final_text="Complete candidate.\n",
                    segments_count=1,
                    verdict="ok",
                    prompt_version="test",
                ),
            )
        ],
        navigation_results=[
            NavigationRunResult(
                ru_path="ydb/docs/ru/toc.yaml",
                en_path="ydb/docs/en/toc.yaml",
                kind="toc",
                target_text="items: []\n",
            )
        ],
        final_tree_blockers=[
            FinalTreeBlocker(
                path="ydb/docs/en/f090.md",
                code="en_link_target",
                message=f"{mode}: unresolved target retained",
            )
        ],
    )

    assert evaluate_publication_impact(result) == PublicationImpact.PUBLISH_RED


def test_F090_no_partial() -> None:
    """A failed required file withholds an accumulated partial candidate."""
    complete = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=_plan(),
                target_text="Complete candidate.\n",
                source_text="Исходный текст.\n",
                file_result=FileTranslationResult(
                    file_path="ydb/docs/en/f090.md",
                    final_text="Complete candidate.\n",
                    segments_count=1,
                    verdict="ok",
                    prompt_version="test",
                ),
            ),
            PairRunResult(plan=_plan(), error="models exhausted"),
        ]
    )

    assert evaluate_publication_impact(complete) == PublicationImpact.WITHHOLD_INCOMPLETE
