"""Human-readable heuristic messages for PR reports."""

from __future__ import annotations

from ydbdoc_review.reporting.heuristic_messages import (
    heuristic_location_label,
    humanize_heuristic,
)


def test_humanize_fence_body_copy():
    raw = "fence_body_copy: block 2 body changed by pipeline (first line: «package main»)"
    text = humanize_heuristic(raw)
    assert "Блок кода №2" in text
    assert "package main" in text
    assert "fence_body_copy" not in text


def test_humanize_cyrillic_in_fence():
    raw = "cyrillic_in_fence: block 1 line 3: «// настройка»"
    text = humanize_heuristic(raw)
    assert "комментарии" in heuristic_location_label(raw)
    assert "кириллица" in text
    assert "блока кода №1" in text


def test_humanize_prose_cyrillic_unchanged():
    raw = "Кириллица в EN-тексте (строка ~5): «привет»"
    assert humanize_heuristic(raw) == raw
