"""Translation pipeline errors."""

from __future__ import annotations


class TranslationError(Exception):
    """Base class for translation errors."""


class TranslationValidationError(TranslationError):
    """LLM output failed structural validation (placeholders, CLI tokens, ids)."""

    def __init__(self, message: str, *, segment_id: str | None = None) -> None:
        super().__init__(message)
        self.segment_id = segment_id
