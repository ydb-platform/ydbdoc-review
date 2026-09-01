"""Human-readable heuristic messages for PR reports."""

from __future__ import annotations

from ydbdoc_review.reporting.heuristic_messages import (
    format_critic_reviewer_detail,
    format_heuristic_reviewer_detail,
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


def test_humanize_orphan_toc_page():
    raw = (
        "orphan_toc_page: translated EN page "
        "`ydb/docs/en/core/concepts/streaming-query/watermarks.md` "
        "is not linked from any EN toc "
        "(reachable from `ydb/docs/en/core/toc_p.yaml` via href/include.path)"
    )
    text = humanize_heuristic(raw)
    assert heuristic_location_label(raw) == "навигация (toc/redirect)"
    assert "не связана" in text
    assert "orphan_toc_page" not in text


def test_format_heuristic_wikipedia_link_locale():
    raw = (
        "link_locale: en.wikipedia.org uses Russian article slug "
        "(use English title): "
        "https://en.wikipedia.org/wiki/%D0%AF%D0%B7%D1%8B%D0%BA_%D0%BC%D0%B0%D0%BD%D0%B8%D0%BF%D1%83%D0%BB%D0%B8%D1%80%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D1%8F_%D0%B4%D0%B0%D0%BD%D0%BD%D1%8B%D0%BC%D0%B8"
    )
    detail = format_heuristic_reviewer_detail(raw)
    assert "Wikipedia" in detail.problem
    assert "русский slug" in detail.problem
    assert detail.suggestion is not None
    assert "en.wikipedia.org" in detail.suggestion


def test_format_critic_execution_failed_invalid_json():
    detail = format_critic_reviewer_detail(
        category="critic_execution_failed",
        comment=(
            "Critic execution failed: Invalid JSON in LLM response: "
            "Expecting value: line 1 column 1 (char 0) | raw_preview='Я не могу'"
        ),
    )
    assert "не парсится как JSON" in detail.problem
    assert "эвристики" in detail.problem
    assert "«Я не могу»" in detail.problem
    assert detail.suggestion is not None
    assert "doc_verify" in detail.suggestion


def test_format_critic_execution_failed_empty_response():
    detail = format_critic_reviewer_detail(
        category="critic_execution_failed",
        comment="Critic execution failed: Empty LLM response",
    )
    assert "пустой ответ" in detail.problem
    assert "batch" in (detail.suggestion or "").casefold()


def test_format_critic_model_refusal():
    detail = format_critic_reviewer_detail(
        category="critic_model_refusal",
        comment=(
            "Model refused critic review; file verified with heuristics only. "
            "Preview: Я не могу обсуждать эту тему."
        ),
    )
    assert "отказала" in detail.problem
    assert "эвристики" in detail.problem
    assert "«Я не могу обсуждать эту тему.»" in detail.problem


def test_humanize_critic_model_refusal_finalize_warning():
    raw = "critic_model_refusal: model declined review; heuristics only on verify"
    text = humanize_heuristic(raw)
    assert "отказала" in text
    assert "эвристики" in text
