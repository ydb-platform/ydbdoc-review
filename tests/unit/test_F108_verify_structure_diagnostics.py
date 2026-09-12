"""F-108: verify diagnoses structural gaps without replacing the target."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.critic_verdict import compute_critic_verdict
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.harness.steps import RoundTripStep, run_critic_loop
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse


def test_F108_no_realign(monkeypatch) -> None:
    source = (
        "# Existing one {#one}\n\nExisting body one.\n\n"
        "## Existing two {#two}\n\nExisting body two.\n\n"
        "## Потерянный раздел {#missing}\n\nНовый текст.\n\n"  # noqa: RUF001
        "## Existing three {#three}\n\nExisting body three.\n\n"
        "## Existing four {#four}\n\nExisting body four.\n"
    )
    target = (
        "# Existing one {#one}\n\nExisting body one.\n\n"
        "## Existing two {#two}\n\nExisting body two.\n\n"
        "## Existing three {#three}\n\nExisting body three.\n\n"
        "## Existing four {#four}\n\nExisting body four.\n"
    )
    source_doc = parse_markdown(source)
    segments = extract_segments(source_doc)
    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/topic.md",
        raw_source_text=source,
        source_text=source,
        existing_target_text=target,
        translated_text=target,
        source_doc=source_doc,
        segments=segments,
    )
    config = load_config(
        env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "key"}
    )
    client = MagicMock(spec=YandexLLMClient)
    context = HarnessContext.from_options(
        client,
        glossary=load_glossary(),
        config=config,
    )

    def _forbidden_translation(*_args, **_kwargs):
        raise AssertionError("verify must not translate a structural gap")

    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.translate_segments",
        _forbidden_translation,
    )

    RoundTripStep().run(state, context)

    assert "Existing body three." in state.translated_text
    assert "Новый текст." not in state.translated_text
    assert state.translated_text != source
    assert state.segment_alignment_error is not None
    assert "Потерянный раздел" in state.segment_alignment_error
    client.chat.assert_not_called()


def test_F108_unapplied() -> None:
    source = "Запустите `cmd`.\n"
    source_doc = parse_markdown(source)
    segments = extract_segments(source_doc)
    segment_id = segments[0].id
    skipped = CriticIssueOut(
        segment_id=segment_id,
        severity="blocked",
        category="placeholder corruption",
        comment="remove the command placeholder",
        suggested_text="Run it.",
    )

    def _review(verification: CriticResponse) -> FileRunState:
        state = FileRunState(
            mode="verify",
            file_path="ydb/docs/ru/topic.md",
            raw_source_text=source,
            source_text=source,
            existing_target_text="Run `cmd`.\n",
            translated_text="Run `cmd`.\n",
            source_doc=source_doc,
            segments=segments,
            translations={segment_id: "Run ⟦C1⟧."},
            render_base_doc=source_doc,
            render_base_segments=segments,
            fence_reference_text="Run `cmd`.\n",
        )
        config = load_config(
            env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "key"}
        )
        context = HarnessContext.from_options(
            MagicMock(spec=YandexLLMClient),
            glossary=load_glossary(),
            config=config,
        )
        with (
            patch(
                "ydbdoc_review.harness.steps.run_critic_pass",
                return_value=CriticResponse(verdict="blocked", issues=[skipped]),
            ),
            patch(
                "ydbdoc_review.harness.steps.run_verify",
                return_value=verification,
            ),
            patch(
                "ydbdoc_review.harness.steps.finalize_en_target",
                side_effect=lambda text, *_args, **_kwargs: text,
            ),
        ):
            run_critic_loop(state, context)
        return state

    unconfirmed = _review(CriticResponse(verdict="ok", issues=[]))
    assert unconfirmed.critic_skipped == [skipped]
    assert unconfirmed.critic_unresolved == CriticResponse(verdict="ok", issues=[])

    confirmed = _review(CriticResponse(verdict="blocked", issues=[skipped]))
    assert confirmed.critic_unresolved == CriticResponse(
        verdict="blocked", issues=[skipped]
    )
    assert (
        compute_critic_verdict(
            initial=confirmed.critic_initial,
            unresolved=confirmed.critic_unresolved,
        )
        == "blocked"
    )
