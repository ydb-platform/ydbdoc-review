"""Tests for segment translator (mocked LLM)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMParseError
from ydbdoc_review.segmentation.chunker import Batch
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.translator import (
    parse_translate_response,
    translate_batch,
    translate_segments,
    validate_segment_translation,
)


def _segment(seg_id: str, text: str) -> Segment:
    return Segment(
        id=seg_id,
        kind=SegmentKind.PARAGRAPH,
        path=["Intro"],
        text=text,
        placeholders=[],
        ast_path=[0],
    )


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


def _json_response(segments: list[dict[str, str]]) -> str:
    return json.dumps({"segments": segments}, ensure_ascii=False)


def test_parse_translate_response_ok():
    raw = _json_response([{"id": "s1", "text": "Hello"}])
    out = parse_translate_response(raw, expected_ids={"s1"})
    assert out == {"s1": "Hello"}


def test_parse_translate_response_id_mismatch():
    raw = _json_response([{"id": "s2", "text": "x"}])
    with pytest.raises(LLMParseError, match="missing ids"):
        parse_translate_response(raw, expected_ids={"s1"})


def test_parse_translate_response_ignores_echoed_input_fields():
    raw = json.dumps(
        {
            "segments": [
                {
                    "id": "s1",
                    "kind": "paragraph",
                    "path": ["Intro"],
                    "text": "Hello",
                }
            ]
        },
        ensure_ascii=False,
    )
    out = parse_translate_response(raw, expected_ids={"s1"})
    assert out == {"s1": "Hello"}


def test_validate_segment_placeholder_mismatch():
    seg = _segment("s1", "Use ⟦C1⟧")
    with pytest.raises(TranslationValidationError, match="placeholder"):
        validate_segment_translation(seg, "Use ⟦C2⟧")


def test_translate_batch_success():
    seg = _segment("s1", "Привет")
    batch = Batch(index=0, segments=[seg])
    client = _mock_client([_json_response([{"id": "s1", "text": "Hello"}])])
    out = translate_batch(
        client, batch, load_glossary(), file_path="docs/ru/x.md"
    )
    assert out == {"s1": "Hello"}


def test_translate_batch_falls_back_to_single_segment():
    seg1 = _segment("s1", "A")
    seg2 = _segment("s2", "B")
    batch = Batch(index=0, segments=[seg1, seg2])
    # Batch call returns wrong ids → parse error → per-segment retry
    client = _mock_client(
        [
            _json_response([{"id": "s1", "text": "only one"}]),
            _json_response([{"id": "s1", "text": "Alpha"}]),
            _json_response([{"id": "s2", "text": "Beta"}]),
        ]
    )
    out = translate_batch(
        client, batch, load_glossary(), file_path="docs/ru/x.md"
    )
    assert out == {"s1": "Alpha", "s2": "Beta"}


def test_translate_segments_uses_cache():
    seg = _segment("s1", "Same text")
    cache: dict[str, str] = {}
    client = _mock_client([_json_response([{"id": "s1", "text": "Cached"}])])

    first = translate_segments(
        [seg],
        client,
        load_glossary(),
        file_path="docs/ru/x.md",
        cache=cache,
    )
    assert first == {"s1": "Cached"}
    assert len(cache) == 1

    # Second call: no new LLM request
    client2 = _mock_client([])
    second = translate_segments(
        [seg],
        client2,
        load_glossary(),
        file_path="docs/ru/x.md",
        cache=cache,
    )
    assert second == {"s1": "Cached"}


def test_translate_batch_rejects_dropped_cli_flag():
    seg = _segment("s1", "Use --yaml-config here")
    batch = Batch(index=0, segments=[seg])
    client = _mock_client(
        [_json_response([{"id": "s1", "text": "Use yaml-config here"}])]
    )
    with pytest.raises(TranslationValidationError, match="CLI"):
        translate_batch(
            client, batch, load_glossary(), file_path="docs/ru/x.md"
        )
