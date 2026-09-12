"""Tests for JSON parsing helpers."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from ydbdoc_review.llm.errors import LLMParseError
from ydbdoc_review.llm.structured import parse_json_content, parse_json_model, strip_code_fences


def test_strip_code_fences_json_tag():
    raw = '```json\n{"a": 1}\n```'
    assert strip_code_fences(raw) == '{"a": 1}'


def test_strip_code_fences_no_tag():
    raw = '```\n{"a": 1}\n```'
    assert strip_code_fences(raw) == '{"a": 1}'


def test_strip_code_fences_plain():
    assert strip_code_fences('{"a": 1}') == '{"a": 1}'


def test_strip_code_fences_non_json_language_tag():
    """Fallback path when opening fence has a language tag other than json."""
    raw = '```python\n{"a": 1}\n```'
    assert strip_code_fences(raw) == '{"a": 1}'


def test_parse_json_content_with_fences():
    raw = '```json\n{"translations": []}\n```'
    assert parse_json_content(raw) == {"translations": []}


def test_parse_json_content_yandex_backtick_wrap():
    raw = '`json\n{"id": "s0001", "text": "hi"}`'
    data = parse_json_content(raw)
    assert data["id"] == "s0001"


def test_parse_json_content_invalid_raises():
    with pytest.raises(LLMParseError, match="Invalid JSON"):
        parse_json_content("not json")


class _Sample(BaseModel):
    id: str
    text: str


def test_parse_json_model_validates():
    raw = '{"id": "s1", "text": "hello"}'
    obj = parse_json_model(raw, _Sample)
    assert obj.id == "s1"
    assert obj.text == "hello"


def test_parse_json_model_validation_error():
    with pytest.raises(LLMParseError, match="schema validation"):
        parse_json_model('{"id": 1}', _Sample)
