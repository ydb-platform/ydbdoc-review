"""F-052: selected files run sequentially and chunk results stay complete."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.pr_context import PRHarnessContext
from ydbdoc_review.harness.pr_state import PRRunState
from ydbdoc_review.harness.pr_steps import ExecutePairPlansStep
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.types import PairRunResult
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.translator import translate_segments


def _plan(index: int) -> PairPlan:
    from ydbdoc_review.pipeline.pairs import DocPair

    pair = DocPair(
        ru_path=f"ydb/docs/ru/page-{index}.md",
        en_path=f"ydb/docs/en/page-{index}.md",
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


def test_F052_sequence_failure():
    plans = [_plan(1), _plan(2), _plan(3)]
    contents = [PairContent(pair=plan.pair, ru_text="RU\n", en_text=None) for plan in plans]
    state = PRRunState(contents=contents, plans=plans)
    ctx = PRHarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}),
    )
    calls: list[str] = []

    def run_one(_content, plan, _ctx, _cache, *, prepare_only=False):
        assert prepare_only is False
        calls.append(plan.target_path)
        return PairRunResult(plan=plan, error="translation chain exhausted")

    with patch("ydbdoc_review.harness.pr_steps.run_pair_plan", side_effect=run_one):
        ExecutePairPlansStep().run(state, ctx)

    assert calls == [plans[0].target_path]
    assert [result.error for result in state.pair_results] == [
        "translation chain exhausted"
    ]


def test_F052_chunks():
    segments = [
        Segment(
            id=f"s{index:04d}",
            kind=SegmentKind.PARAGRAPH,
            path=["Body"],
            text=f"English segment {index}.",
            placeholders=[],
            ast_path=[index],
        )
        for index in range(7)
    ]
    calls: list[list[str]] = []

    def translate_batch(_client, batch, _glossary, **_kwargs):
        calls.append([segment.id for segment in batch.segments])
        return {segment.id: segment.text for segment in batch.segments}

    with patch("ydbdoc_review.translation.translator.translate_batch", side_effect=translate_batch):
        result = translate_segments(
            segments,
            SimpleNamespace(),
            load_glossary(),
            file_path="ydb/docs/ru/page.md",
            max_parallel_batches=1,
            segment_max_chars=24,
        )

    flattened = [segment_id for batch in calls for segment_id in batch]
    assert flattened == [segment.id for segment in segments]
    assert sorted(result) == sorted(flattened)
    assert len(calls) <= len(segments)
