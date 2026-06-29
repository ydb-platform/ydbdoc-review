"""Tests for per-file harness (translate / verify profiles)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness import (
    FileHarness,
    FileRunState,
    HarnessContext,
    TRANSLATE_PROFILE,
    VERIFY_PROFILE,
)
from ydbdoc_review.harness.steps import ParseStep, TranslateStep
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.translate_file import translate_file
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary


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


def test_profiles_share_qa_tail():
    translate_names = [s.name for s in TRANSLATE_PROFILE.steps]
    verify_names = [s.name for s in VERIFY_PROFILE.steps]
    assert translate_names[0] == "parse"
    assert verify_names[0] == "parse"
    assert translate_names[1] == "translate"
    assert verify_names[1] == "load_target"
    assert "critic_feedback_retry" in translate_names
    assert "critic_feedback_retry" not in verify_names
    shared_qa = ["round_trip", "critic_loop"]
    assert translate_names[2:4] == shared_qa
    assert verify_names[2:4] == shared_qa
    assert translate_names[-3:] == verify_names[-3:] == [
        "heuristics",
        "verdict",
        "report_artifacts",
    ]


def test_parse_step_empty_file_stops_early():
    state = FileRunState(
        mode="translate",
        file_path="empty.md",
        raw_source_text="",
        source_text="",
    )
    ctx = HarnessContext.from_options(
        _mock_client([]),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"}),
    )
    ParseStep().run(state, ctx)
    assert state.stopped_early is True
    assert state.segments == []


def test_harness_translate_matches_translate_file():
    source = "Привет.\n"
    segments = extract_segments(parse_markdown(source))
    seg_id = segments[0].id
    responses = [
        _translate_json(segments, {seg_id: "Hello.\n"}),
        json.dumps({"verdict": "ok", "issues": []}),
    ]
    glossary = load_glossary()
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"})

    state = FileRunState(
        mode="translate",
        file_path="docs/ru/a.md",
        raw_source_text=source,
        source_text=source,
    )
    harness_result = FileHarness(TRANSLATE_PROFILE).run(
        state, HarnessContext.from_options(_mock_client(responses), glossary=glossary, config=cfg)
    )

    file_result = translate_file(
        source,
        _mock_client(responses),
        glossary,
        file_path="docs/ru/a.md",
        config=cfg,
    )

    assert harness_result.verdict == file_result.verdict
    assert harness_result.final_text == file_result.final_text
    assert harness_result.segments_count == file_result.segments_count


def test_translate_step_skipped_in_verify_profile():
    source = "Привет.\n"
    state = FileRunState(
        mode="verify",
        file_path="docs/ru/a.md",
        raw_source_text=source,
        source_text=source,
        existing_target_text="Hello.\n",
    )
    ctx = HarnessContext.from_options(
        _mock_client([]),
        config=load_config(env={"YDBDOC_YC_FOLDER_ID": "b1x", "YDBDOC_YC_API_KEY": "k"}),
    )
    with patch("ydbdoc_review.harness.steps.translate_segments") as mock_tr:
        TranslateStep().run(state, ctx)
        mock_tr.assert_not_called()
