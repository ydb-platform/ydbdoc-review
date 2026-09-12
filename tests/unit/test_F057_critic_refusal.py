"""F-057: critic policy refusal is informational; technical failure is RED."""

from __future__ import annotations

from ydbdoc_review.github.workflow import DocJobResult, job_requires_nonzero_exit
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.publication import refresh_publication_impact
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse


def _plan() -> PairPlan:
    pair = DocPair(
        ru_path="ydb/docs/ru/f057.md",
        en_path="ydb/docs/en/f057.md",
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


def _file_result(critic: CriticResponse) -> FileTranslationResult:
    return FileTranslationResult(
        file_path="ydb/docs/en/f057.md",
        final_text="Complete candidate.\n",
        segments_count=1,
        verdict="blocked" if critic.verdict == "blocked" else "ok",
        prompt_version="test",
        critic_unresolved=critic,
    )


def test_F057_policy() -> None:
    refusal = CriticResponse(
        verdict="ok",
        issues=[
            CriticIssueOut(
                severity="info",
                category="critic_model_refusal",
                comment="content policy refusal",
            )
        ],
    )
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=_plan(),
                target_text="Complete candidate.\n",
                source_text="Исходный текст.\n",
                file_result=_file_result(refusal),
            )
        ]
    )

    assert refresh_publication_impact(result) == PublicationImpact.PUBLISH_NORMAL
    assert result.pair_results[0].target_text
    assert result.pair_results[0].error is None


def test_F057_technical() -> None:
    failure = CriticResponse(
        verdict="blocked",
        issues=[
            CriticIssueOut(
                severity="blocked",
                category="critic_execution_failed",
                comment="Critic execution failed after retries",
            )
        ],
    )
    pair_result = PairRunResult(
        plan=_plan(),
        target_text="Complete candidate.\n",
        source_text="Исходный текст.\n",
        file_result=_file_result(failure),
    )
    result = PRTranslationResult(pair_results=[pair_result])

    assert refresh_publication_impact(result) == PublicationImpact.PUBLISH_RED
    assert pair_result.target_text == "Complete candidate.\n"
    job = DocJobResult(
        mode="doc_translate",
        pr_number=1,
        translation_pr_number=99,
        pr_result=result,
    )
    assert job_requires_nonzero_exit(job) is False
