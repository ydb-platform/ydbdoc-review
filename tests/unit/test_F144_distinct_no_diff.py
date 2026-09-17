"""F-144 contracts for distinct no-op, mechanical, and no-diff outcomes."""

from __future__ import annotations

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.pipeline.analyze import PairPlan
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.pipeline.types import (
    NavigationRunResult,
    PairRunResult,
    PRTranslationResult,
)
from ydbdoc_review.reporting.builder import ReportMeta, build_source_pr_comment


def _cfg():
    return load_config(env={"YDBDOC_YC_FOLDER_ID": "folder", "YDBDOC_YC_API_KEY": "key"})


def _noop() -> PRTranslationResult:
    plan = PairPlan(
        pair=DocPair("ru/a.md", "en/a.md"),
        action="critic_only",
        source_path="ru/a.md",
        target_path="en/a.md",
        source_lang="ru",
        target_lang="en",
        summary="Analyze подтвердил полное совпадение.",
    )
    return PRTranslationResult(pair_results=[PairRunResult(plan=plan, skipped=True)])


def _meta() -> ReportMeta:
    return ReportMeta(mode="doc_translate", report_number=144, elapsed_s=1)


def test_F144_three_cases() -> None:
    noop_body = build_source_pr_comment(
        _noop(), translation_pr_number=None, meta=_meta(), config=_cfg()
    )
    assert "Analyze подтвердил" in noop_body
    assert "Translation PR не создаётся" in noop_body

    mechanical = PRTranslationResult(
        navigation_results=[
            NavigationRunResult("ru/toc.yaml", "en/toc.yaml", "toc", target_text="items:\n")
        ]
    )
    mechanical_body = build_source_pr_comment(
        mechanical, translation_pr_number=77, meta=_meta(), config=_cfg(), committed=True
    )
    assert "Analyze подтвердил" not in mechanical_body
    assert "#77" in mechanical_body

    no_diff_body = build_source_pr_comment(
        PRTranslationResult(),
        translation_pr_number=None,
        meta=_meta(),
        config=_cfg(),
        committed=False,
    )
    assert "нет коммита" in no_diff_body
    assert "Translation PR не создаётся" in no_diff_body


def test_F144_unresolved() -> None:
    unresolved = PRTranslationResult(
        publication_failure="awaiting_instruction_no_artifact"
    )
    waiting = build_source_pr_comment(
        unresolved, translation_pr_number=None, meta=_meta(), config=_cfg()
    )
    assert "awaiting_instruction_no_artifact" in waiting
    assert "фиктивным commit/PR" in waiting

    red_pr = build_source_pr_comment(
        unresolved, translation_pr_number=78, meta=_meta(), config=_cfg()
    )
    assert "published_red" in red_pr
    assert "#78" in red_pr
