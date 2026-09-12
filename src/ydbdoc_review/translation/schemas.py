"""Pydantic schemas for LLM translation JSON I/O."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TranslatedSegmentOut(BaseModel):
    """One segment in a translator response."""

    # LLMs often echo input fields (kind, path) — ignore extras, keep id+text.
    model_config = ConfigDict(extra="ignore")
    id: str
    text: str


class TranslateBatchResponse(BaseModel):
    """Expected top-level JSON from the translator LLM."""

    model_config = ConfigDict(extra="forbid")
    segments: list[TranslatedSegmentOut] = Field(min_length=1)


CriticVerdict = Literal["ok", "warnings", "blocked"]
CriticSeverity = Literal["warning", "blocked", "info"]


class CriticIssueOut(BaseModel):
    """One issue from critic or verify pass."""

    model_config = ConfigDict(extra="ignore")
    segment_id: str | None = None
    severity: CriticSeverity
    category: str
    comment: str
    suggested_text: str | None = None


class CriticResponse(BaseModel):
    """Expected top-level JSON from critic / verify LLM."""

    model_config = ConfigDict(extra="forbid")
    verdict: CriticVerdict
    issues: list[CriticIssueOut] = Field(default_factory=list)


AnalyzeTarget = Literal["en", "ru"]


class AnalyzePairResult(BaseModel):
    """One pair from pre-analyze LLM."""

    model_config = ConfigDict(extra="forbid")
    ru_path: str
    en_path: str
    ru_present: bool
    en_present: bool
    semantically_aligned: bool
    needs_generation_for: AnalyzeTarget | None = None
    summary: str


class AnalyzeBatchResponse(BaseModel):
    """Top-level JSON from analyze prompt."""

    model_config = ConfigDict(extra="forbid")
    results: list[AnalyzePairResult]
