"""Pydantic schemas for LLM translation JSON I/O."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TranslatedSegmentOut(BaseModel):
    """One segment in a translator response."""

    model_config = ConfigDict(extra="forbid")
    id: str
    text: str


class TranslateBatchResponse(BaseModel):
    """Expected top-level JSON from the translator LLM."""

    model_config = ConfigDict(extra="forbid")
    segments: list[TranslatedSegmentOut] = Field(min_length=1)
