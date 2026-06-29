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


def test_humanize_md_link_parity():
    raw = "md_link_parity: EN missing RU links: backup.md, system-tablet-backup.md"
    text = humanize_heuristic(raw)
    assert heuristic_location_label(raw) == "ссылки"
    assert "backup.md" in text
    assert "md_link_parity" not in text


def test_humanize_unexpected_href():
    raw = (
        "unexpected_href: EN toc has hrefs not in RU PR and not EN legacy: "
        "['href:recipes/system-tablet-backup/index.md']"
    )
    text = humanize_heuristic(raw)
    assert "diff RU PR" in text
    assert "system-tablet-backup" in text
