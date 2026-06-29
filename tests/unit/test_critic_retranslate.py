"""Tests for critic-guided segment re-translation."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.harness.steps import CriticFeedbackRetryStep
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.translate_file import translate_file
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse


def _completion(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _mock_client(responses: list[str]) -> YandexLLMClient:
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.side_effect = [
        _completion(r) for r in responses
    ]
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"})
    return YandexLLMClient(
        folder_id="b1x",
        api_key="k",
        llm=cfg.llm,
        client=mock_openai,
    )


def _translate_json(segments, mapping: dict[str, str]) -> str:
    payload = {
        "segments": [
            {"id": seg.id, "text": mapping.get(seg.id, seg.text)}
            for seg in segments
        ]
    }
    return json.dumps(payload, ensure_ascii=False)


def test_critic_feedback_retry_step_retranslates_unresolved():
    source = "Неверный перевод.\n"
    doc = parse_markdown(source)
    segments = extract_segments(doc)
    seg_id = segments[0].id

    retry_translate = _translate_json(segments, {seg_id: "Correct translation."})
    critic_ok = json.dumps({"verdict": "ok", "issues": []})

    cfg = load_config(
        env={
            "YDBDOC_YC_FOLDER_ID": "b1x",
            "YDBDOC_YC_API_KEY": "k",
            "YDBDOC_TRANSLATION_CRITIC_FEEDBACK_RETRIES": "1",
        }
    )
    client = _mock_client([retry_translate, critic_ok])
    ctx = HarnessContext.from_options(client, glossary=load_glossary(), config=cfg)

    state = FileRunState(
        mode="translate",
        file_path="docs/ru/a.md",
        raw_source_text=source,
        source_text=source,
        source_doc=doc,
        segments=segments,
        translations={seg_id: "Wrong translation."},
        translated_text="Wrong translation.\n",
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
                    comment="still wrong",
                    suggested_text=None,
                )
            ],
        ),
    )

    CriticFeedbackRetryStep().run(state, ctx)

    assert state.translate_retry_count == 1
    assert state.translations[seg_id] == "Correct translation."
    assert state.critic_unresolved is not None
    assert state.critic_unresolved.issues == []


def test_translate_file_critic_feedback_retry_end_to_end():
    source = "Неверный перевод.\n"
    segments = extract_segments(parse_markdown(source))
    seg_id = segments[0].id

    translate_raw = _translate_json(segments, {seg_id: "Wrong translation."})
    critic_raw = json.dumps(
        {
            "verdict": "blocked",
            "issues": [
                {
                    "segment_id": seg_id,
                    "severity": "blocked",
                    "category": "meaning",
                    "comment": "meaning lost",
                    "suggested_text": None,
                }
            ],
        }
    )
    verify_raw = json.dumps(
        {
            "verdict": "blocked",
            "issues": [
                {
                    "segment_id": seg_id,
                    "severity": "blocked",
                    "category": "meaning",
                    "comment": "still wrong",
                    "suggested_text": None,
                }
            ],
        }
    )
    retry_raw = _translate_json(segments, {seg_id: "Correct translation."})
    critic_ok = json.dumps({"verdict": "ok", "issues": []})

    cfg = load_config(
        env={
            "YDBDOC_YC_FOLDER_ID": "b1x",
            "YDBDOC_YC_API_KEY": "k",
            "YDBDOC_TRANSLATION_CRITIC_FEEDBACK_RETRIES": "1",
        }
    )
    client = _mock_client(
        [translate_raw, critic_raw, verify_raw, retry_raw, critic_ok]
    )
    result = translate_file(
        source,
        client,
        load_glossary(),
        file_path="docs/ru/a.md",
        config=cfg,
    )

    assert "Correct translation." in result.final_text
    assert result.verdict == "ok"
    assert result.critic_unresolved is not None
    assert result.critic_unresolved.issues == []


def test_critic_feedback_retry_skipped_when_disabled():
    source = "Проблема.\n"
    segments = extract_segments(parse_markdown(source))
    seg_id = segments[0].id

    state = FileRunState(
        mode="translate",
        file_path="docs/ru/a.md",
        raw_source_text=source,
        source_text=source,
        segments=segments,
        translations={seg_id: "Problem."},
        critic_unresolved=CriticResponse(
            verdict="blocked",
            issues=[
                CriticIssueOut(
                    segment_id=seg_id,
                    severity="blocked",
                    category="meaning",
                    comment="bad",
                    suggested_text=None,
                )
            ],
        ),
    )
    cfg = load_config(
        env={
            "YDBDOC_YC_FOLDER_ID": "b1x",
            "YDBDOC_YC_API_KEY": "k",
            "YDBDOC_TRANSLATION_CRITIC_FEEDBACK_RETRIES": "0",
        }
    )
    ctx = HarnessContext.from_options(
        _mock_client([]), glossary=load_glossary(), config=cfg
    )

    CriticFeedbackRetryStep().run(state, ctx)

    assert state.translate_retry_count == 0
    assert state.translations[seg_id] == "Problem."
