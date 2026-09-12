"""F-102: doc_verify repairs existing targets without translating files."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.pr_context import PRHarnessContext
from ydbdoc_review.harness.pr_profiles import VERIFY_PR_PROFILE
from ydbdoc_review.harness.pr_runner import PRHarness
from ydbdoc_review.harness.pr_state import PRRunState
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.harness.steps import RoundTripStep, run_critic_loop
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.analyze import PairContent
from ydbdoc_review.pipeline.completeness import completeness_gaps
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse


def _config():
    return load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})


def _issue(segment_id: str, suggestion: str) -> CriticIssueOut:
    return CriticIssueOut(
        segment_id=segment_id,
        severity="warning",
        category="meaning",
        comment="repair this segment",
        suggested_text=suggestion,
    )


def test_F102_local_rounds() -> None:
    source = "Исходный текст.\n"
    doc = parse_markdown(source)
    segments = extract_segments(doc)
    segment_id = segments[0].id
    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/topic.md",
        raw_source_text=source,
        source_text=source,
        existing_target_text="Existing target.\n",
        source_doc=doc,
        segments=segments,
        translations={segment_id: "Existing target."},
        translated_text="Existing target.\n",
        render_base_doc=doc,
        render_base_segments=segments,
        fence_reference_text="Existing target.\n",
    )
    ctx = HarnessContext.from_options(
        MagicMock(spec=YandexLLMClient),
        glossary=load_glossary(),
        config=_config(),
    )

    with (
        patch(
            "ydbdoc_review.harness.steps.run_critic_pass",
            return_value=CriticResponse(
                verdict="warnings",
                issues=[_issue(segment_id, "First local repair.")],
            ),
        ),
        patch(
            "ydbdoc_review.harness.steps.run_verify",
            side_effect=(
                CriticResponse(
                    verdict="warnings",
                    issues=[_issue(segment_id, "Second local repair.")],
                ),
                CriticResponse(verdict="ok", issues=[]),
            ),
        ) as verify,
        patch(
            "ydbdoc_review.harness.steps.render_with_translations",
            side_effect=lambda _doc, _segments, translations, **_kwargs: (
                f"{translations[segment_id]}\n"
            ),
        ),
        patch(
            "ydbdoc_review.harness.steps.finalize_en_target",
            side_effect=lambda text, *_args, **_kwargs: text,
        ),
        patch(
            "ydbdoc_review.harness.steps.gate_round_trip",
            side_effect=lambda _segments, text: ({segment_id: text.strip()}, None),
        ),
        patch("ydbdoc_review.harness.steps.translate_segments") as full_translate,
    ):
        run_critic_loop(state, ctx)

    assert state.translated_text == "Second local repair.\n"
    assert [issue.suggested_text for issue in state.critic_applied] == [
        "First local repair.",
        "Second local repair.",
    ]
    assert state.critic_unresolved == CriticResponse(verdict="ok", issues=[])
    assert verify.call_count == 2
    full_translate.assert_not_called()

    mismatch = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/topic.md",
        raw_source_text=source,
        source_text=source,
        existing_target_text="Existing target.\n",
        source_doc=doc,
        segments=segments,
        translated_text="Existing target.\n",
    )
    with (
        patch(
            "ydbdoc_review.harness.steps.gate_round_trip",
            return_value=({}, "segment alignment mismatch"),
        ),
        patch(
            "ydbdoc_review.harness.steps.translate_segments",
            side_effect=AssertionError("doc_verify must not regenerate a file"),
        ) as full_translate,
    ):
        RoundTripStep().run(mismatch, ctx)

    assert mismatch.translated_text == "Existing target.\n"
    assert mismatch.segment_alignment_error == "segment alignment mismatch"
    full_translate.assert_not_called()


def test_F102_missing_pair() -> None:
    missing = DocPair(
        ru_path="ydb/docs/ru/missing.md",
        en_path="ydb/docs/en/missing.md",
        ru_changed=True,
    )
    glossary = DocPair(
        ru_path="ydb/docs/ru/concepts/glossary.md",
        en_path="ydb/docs/en/concepts/glossary.md",
        ru_changed=True,
    )
    state = PRRunState(
        contents=[
            PairContent(pair=missing, ru_text="Новая страница.\n", en_text=None),
            PairContent(
                pair=glossary,
                ru_text="Термин.\n",
                en_text="Existing glossary.\n",
            ),
        ]
    )
    client = MagicMock(spec=YandexLLMClient)
    client.usage_tracker.records = []
    ctx = PRHarnessContext.from_options(
        client,
        glossary=load_glossary(),
        config=_config(),
    )

    with patch("ydbdoc_review.harness.steps.translate_segments") as full_translate:
        result = PRHarness(VERIFY_PR_PROFILE).run(state, ctx)

    missing_result, glossary_result = result.pair_results
    assert missing_result.skipped
    assert missing_result.target_text is None
    assert completeness_gaps(
        [(missing.ru_path, "modified")],
        result,
    ) == [missing.en_path]
    assert glossary_result.target_text == "Existing glossary.\n"
    client.chat.assert_not_called()
    full_translate.assert_not_called()
