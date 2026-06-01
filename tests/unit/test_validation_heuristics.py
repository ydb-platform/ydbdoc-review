"""Tests for Phase E validation heuristics."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.validation.heuristics import (
    bump_verdict_for_heuristics,
    check_cyrillic_in_en,
    check_fence_parity,
    check_heading_parity,
    check_length_ratio,
    run_file_heuristics,
    validate_navigation_merge_warnings,
)


def test_cyrillic_in_en_detects_prose():
    warnings = check_cyrillic_in_en("Hello привет world", target_lang="en")
    assert len(warnings) == 1
    assert "cyrillic_in_en" in warnings[0]


def test_cyrillic_in_en_ignores_fenced_code():
    text = "Intro\n\n```\nпривет\n```\n"
    assert check_cyrillic_in_en(text, target_lang="en") == []


def test_cyrillic_skipped_for_ru_target():
    assert check_cyrillic_in_en("привет", target_lang="ru") == []


def test_fence_parity_mismatch():
    src = "A\n\n```\ncode\n```\n"
    tgt = "A\n"
    warnings = check_fence_parity(src, tgt)
    assert any("fence_parity" in w for w in warnings)


def test_heading_parity():
    src = "# One\n\n## Two\n"
    tgt = "# One\n"
    assert check_heading_parity(src, tgt)


def test_length_ratio_short_text_skipped():
    assert check_length_ratio("Hi", "Hello", source_lang="ru", target_lang="en") == []


def test_length_ratio_out_of_bounds():
    src = "word " * 50
    tgt = "x" * 45
    warnings = check_length_ratio(src, tgt, source_lang="ru", target_lang="en")
    assert warnings and "length_ratio" in warnings[0]


def test_run_file_heuristics_combined():
    src = "# Title\n\n" + ("Paragraph. " * 30) + "\n\n```\nru\n```\n"
    tgt = "# Title\n\n" + ("Paragraph. " * 30) + "\n\n```\nen\n```\n"
    warnings = run_file_heuristics(src, tgt, source_lang="ru", target_lang="en")
    assert isinstance(warnings, list)


def test_bump_verdict_for_heuristics():
    assert bump_verdict_for_heuristics("ok", ["x"]) == "warnings"
    assert bump_verdict_for_heuristics("blocked", ["x"]) == "blocked"


def test_validate_navigation_merge_warnings_toc():
    ru = dedent("""
        items:
        - name: A
          href: a.md
    """).strip()
    en = ru.replace("name: A", "name: B")
    warnings = validate_navigation_merge_warnings(
        "ydb/docs/ru/toc.yaml",
        ru,
        en,
        en_main_yaml=ru,
        translate_scope=set(),
    )
    assert isinstance(warnings, list)
