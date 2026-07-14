"""Tests for heuristic context extraction in reports."""

from __future__ import annotations

from ydbdoc_review.reporting.heuristic_context import heuristic_context_for_message


def test_heuristic_context_finds_wikipedia_line_in_target():
    href = (
        "https://en.wikipedia.org/wiki/"
        "%D0%AF%D0%B7%D1%8B%D0%BA_%D0%BC%D0%B0%D0%BD%D0%B8%D0%BF%D1%83%D0%BB%D0%B8%D1%80%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D1%8F_%D0%B4%D0%B0%D0%BD%D0%BD%D1%8B%D0%BC%D0%B8"
    )
    target = (
        "Intro paragraph.\n\n"
        f"See [DML]({href}) for details.\n"
    )
    message = (
        "link_locale: en.wikipedia.org uses Russian article slug "
        f"(use English title): {href}"
    )
    ctx = heuristic_context_for_message(
        message,
        target_text=target,
        segment_source_excerpts={
            "s0001": "См. [язык манипулирования данными](https://ru.wikipedia.org/wiki/Язык_манипулирования_данными)."
        },
    )
    assert ctx.line_range == (3, 3)
    assert "DML" in (ctx.target_excerpt or "")
    assert "wikipedia" in (ctx.source_excerpt or "").lower()
