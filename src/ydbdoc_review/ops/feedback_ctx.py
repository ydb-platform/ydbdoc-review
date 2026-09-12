"""Process-local continue feedback for prompt injection."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_continue_feedback: ContextVar[str] = ContextVar("ydbdoc_continue_feedback", default="")


def get_continue_feedback() -> str:
    return _continue_feedback.get() or ""


@contextmanager
def continue_feedback_scope(text: str | None) -> Iterator[None]:
    token = _continue_feedback.set((text or "").strip())
    try:
        yield
    finally:
        _continue_feedback.reset(token)
