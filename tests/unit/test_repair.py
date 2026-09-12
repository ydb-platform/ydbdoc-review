"""Tests for focused repair pass after translate validation failures."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.glossary import load_glossary
from ydbdoc_review.translation.repair import repair_segment_translation
from ydbdoc_review.translation.translator import translate_batch, translate_segments
from ydbdoc_review.segmentation.chunker import Batch
from ydbdoc_review.pipeline.types import ManualAction


def _json_response(segments: list[dict]) -> MagicMock:
    import json

    content = json.dumps({"segments": segments})
    result = MagicMock()
    result.content = content
    return result


def _mock_client(responses: list[MagicMock]) -> MagicMock:
    client = MagicMock()
    client.model_chain_for_role.return_value = ["primary", "fallback"]
    client.chat.side_effect = responses
    client.usage_tracker = MagicMock()
    return client


def test_repair_segment_translation_success():
    seg = Segment(
        id="s1",
        kind=SegmentKind.TABLE_BODY_CELL,
        path=["table:row1:col2"],
        text="Режим `⟦C1⟧` и [ссылка](⟦U1⟧)",
        placeholders=[],
        ast_path=[0],
    )
    good = _json_response(
        [{"id": "s1", "text": "Batching mode `⟦C1⟧` and [link](⟦U1⟧)"}]
    )
    client = _mock_client([good])
    out = repair_segment_translation(
        client,
        seg,
        load_glossary(),
        validation_error="placeholder mismatch",
        failed_attempt="broken",
        file_path="docs/ru/x.md",
    )
    assert out is not None
    assert "⟦C1⟧" in out


def test_translate_batch_uses_repair_before_table_fallback():
    seg = Segment(
        id="s1",
        kind=SegmentKind.TABLE_BODY_CELL,
        path=["table:row1:col2"],
        text="Значение `⟦C1⟧`",
        placeholders=[],
        ast_path=[0],
    )
    batch = Batch(index=0, segments=[seg])
    bad = _json_response([{"id": "s1", "text": "Value only"}])
    repaired = _json_response([{"id": "s1", "text": "Value `⟦C1⟧`"}])
    client = _mock_client([bad, bad, bad, repaired])
    client.model_chain_for_role.return_value = ["primary"]
    out = translate_batch(
        client, batch, load_glossary(), file_path="docs/ru/x.md"
    )
    assert out == {"s1": "Value `⟦C1⟧`"}


def test_translate_segments_manual_action_when_repair_fails():
    seg = Segment(
        id="s1",
        kind=SegmentKind.TABLE_BODY_CELL,
        path=["table:row1:col2"],
        text="Значение `⟦C1⟧`",
        placeholders=[],
        ast_path=[0],
    )
    bad = _json_response([{"id": "s1", "text": "Value only"}])
    client = _mock_client([bad] * 8)
    notes: list[ManualAction] = []
    out = translate_segments(
        [seg],
        client,
        load_glossary(),
        file_path="docs/ru/x.md",
        manual_actions=notes,
    )
    assert out == {"s1": seg.text}
    assert len(notes) == 1
    assert notes[0].segment_id == "s1"
    assert "Переведите вручную" in notes[0].message
