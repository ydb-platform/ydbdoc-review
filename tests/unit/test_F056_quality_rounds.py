"""F-056: critic quality loop has two bounded repairs and keeps candidates."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import QUALITY_REPAIR_ROUNDS, HarnessContext
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.harness.steps import CriticFeedbackRetryStep, CriticLoopStep
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.translate_file import translate_file
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse


def _state() -> FileRunState:
    source = "Неверный перевод.\n"
    doc = parse_markdown(source)
    segments = extract_segments(doc)
    seg_id = segments[0].id
    return FileRunState(
        mode="translate",
        file_path="docs/ru/f056.md",
        raw_source_text=source,
        source_text=source,
        source_doc=doc,
        segments=segments,
        translations={seg_id: "Last complete candidate."},
        translated_text="Last complete candidate.\n",
        render_base_doc=doc,
        render_base_segments=segments,
        fence_reference_text=source,
        critic_unresolved=CriticResponse(
            verdict="blocked",
            issues=[
                CriticIssueOut(
                    segment_id=seg_id,
                    severity="blocked",
                    category="meaning",
                    comment="needs repair",
                )
            ],
        ),
    )


def _ctx() -> HarnessContext:
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    return HarnessContext.from_options(
        MagicMock(spec=YandexLLMClient),
        glossary=load_glossary(),
        config=cfg,
        critic_feedback_retries=99,
    )


def test_F056_rounds() -> None:
    state = _state()
    calls: list[str] = []
    needs = iter((True, True, False))

    with (
        patch(
            "ydbdoc_review.harness.steps._needs_critic_feedback_retranslate",
            side_effect=lambda _state: next(needs),
        ),
        patch(
            "ydbdoc_review.harness.steps._unresolved_retry_segment_ids",
            return_value={state.segments[0].id},
        ),
        patch("ydbdoc_review.harness.steps.issues_by_segment_id", return_value={}),
        patch(
            "ydbdoc_review.harness.steps.retranslate_segments_with_critic_feedback",
            side_effect=lambda *args, **kwargs: (
                calls.append("repair") or state.translations
            ),
        ),
        patch("ydbdoc_review.harness.steps._render_translated_from_source"),
        patch(
            "ydbdoc_review.harness.steps.gate_round_trip",
            side_effect=lambda _segments, _text: (state.translations, None),
        ),
        patch(
            "ydbdoc_review.harness.steps.run_critic_loop",
            side_effect=lambda _state, _ctx: calls.append("critic"),
        ),
    ):
        CriticFeedbackRetryStep().run(state, _ctx())

    assert calls == ["repair", "critic", "repair", "critic"]
    assert state.translate_retry_count == QUALITY_REPAIR_ROUNDS == 2


def test_F056_repair_failure() -> None:
    state = _state()
    original = dict(state.translations)
    with (
        patch(
            "ydbdoc_review.harness.steps._needs_critic_feedback_retranslate",
            side_effect=(True, True, False),
        ),
        patch(
            "ydbdoc_review.harness.steps._unresolved_retry_segment_ids",
            return_value={state.segments[0].id},
        ),
        patch("ydbdoc_review.harness.steps.issues_by_segment_id", return_value={}),
        patch(
            "ydbdoc_review.harness.steps.retranslate_segments_with_critic_feedback",
            return_value=original,
        ),
        patch("ydbdoc_review.harness.steps._render_translated_from_source"),
        patch(
            "ydbdoc_review.harness.steps.gate_round_trip",
            return_value=(original, None),
        ),
        patch("ydbdoc_review.harness.steps.run_critic_loop"),
    ):
        CriticFeedbackRetryStep().run(state, _ctx())

    assert state.translations == original
    assert state.translate_retry_count == 2
    assert state.critic_unresolved is not None
    assert state.critic_unresolved.issues


def test_F056_special_files() -> None:
    state = _state()
    state.mode = "verify"
    state.file_path = "docs/ru/concepts/glossary.md"
    with patch("ydbdoc_review.harness.steps.run_critic_loop") as run_critic:
        CriticLoopStep().run(state, _ctx())
    run_critic.assert_not_called()
    assert any("glossary_verify_critic_skipped" in w for w in state.finalize_warnings)

    client = MagicMock(spec=YandexLLMClient)
    result = translate_file(
        "```bash\necho hi\n```\n",
        client,
        load_glossary(),
        file_path="docs/ru/code.md",
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"}),
    )
    assert result.segments_count == 0
    client.chat.assert_not_called()
