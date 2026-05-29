"""Parse JSON from LLM responses (including fenced output)."""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ydbdoc_review.llm.errors import LLMParseError

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n(?P<body>.*?)\n?\s*```\s*$",
    re.DOTALL,
)


def strip_code_fences(text: str) -> str:
    """Remove optional markdown code fences around model output."""
    s = text.strip()
    match = _FENCE_RE.match(s)
    if match:
        return match.group("body").strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            body = s[first_nl + 1 :]
            if body.rstrip().endswith("```"):
                body = body.rstrip()[: body.rstrip().rfind("```")]
            return body.strip()
    return s


def parse_json_content(raw: str) -> Any:
    """Parse JSON from raw LLM text, stripping fences and stray backticks."""
    text = strip_code_fences(raw).strip()
    if text.startswith("`") and text.endswith("`"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"Invalid JSON in LLM response: {exc}") from exc


def parse_json_model(raw: str, model: type[T]) -> T:
    """Parse and validate LLM output against a pydantic model."""
    data = parse_json_content(raw)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise LLMParseError(f"JSON schema validation failed: {exc}") from exc
