"""Warnings when finalize-time LLM passes fail-soft skip residual Cyrillic."""

from __future__ import annotations

from ydbdoc_review.llm.retry import is_rate_limit_error

_SKIP_PREFIXES = (
    "fence_comment_translate_skipped:",
    "text_fence_translate_skipped:",
    "prose_cyrillic_translate_skipped:",
)


def finalize_translate_skip_warning(kind: str, exc: Exception) -> str:
    """Human-readable heuristic warning for a skipped finalize LLM pass."""
    label = kind.strip().replace(" ", "_")
    if is_rate_limit_error(exc):
        return f"{label}_translate_skipped: rate-limit — {exc}"
    return f"{label}_translate_skipped: {exc}"


def is_finalize_translate_skip_warning(message: str) -> bool:
    return message.startswith(_SKIP_PREFIXES)
