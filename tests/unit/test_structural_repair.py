"""Tests for §6.191 structural EN repairs (#49957)."""

from __future__ import annotations

from ydbdoc_review.validation.structural_repair import (
    repair_en_structure_from_ru,
    restore_explicit_heading_anchors,
    sync_missing_signature_sections,
)


def test_restore_explicit_heading_anchors_csharp_app():
    ru = "# Приложение на C# {#csharp-app}\n\nBody.\n"
    en = "# Example app in C# (.NET)\n\nBody.\n"
    fixed = restore_explicit_heading_anchors(en, ru)
    assert "{#csharp-app}" in fixed
    assert fixed.startswith("# Example app in C# (.NET) {#csharp-app}")


def test_sync_missing_signature_sections_inserts_yql_block():
    ru = (
        "## COUNT {#count}\n\n"
        "### Сигнатура\n\n"
        "```yql\n"
        "COUNT(*)->Uint64\n"
        "COUNT(T)->Uint64\n"
        "```\n\n"
        "Подсчет строк.\n\n"
        "### Примеры\n\n"
        "```yql\nSELECT COUNT(*) FROM t;\n```\n"
    )
    en = (
        "## COUNT {#count}\n\n"
        "Count rows.\n\n"
        "### Examples\n\n"
        "```yql\nSELECT COUNT(*) FROM t;\n```\n"
    )
    fixed = sync_missing_signature_sections(en, ru)
    assert "### Signature\n\n" in fixed
    assert "COUNT(*)->Uint64" in fixed
    assert fixed.index("### Signature") < fixed.index("Count rows.")


def test_repair_en_structure_from_ru_combined():
    ru = (
        "# Title {#t}\n\n"
        "## MIN {#min}\n\n"
        "### Сигнатура\n\n"
        "```yql\nMIN(T)->T?\n```\n\n"
        "Minimum.\n"
    )
    en = "# Title\n\n## MIN {#min}\n\nMinimum.\n"
    fixed = repair_en_structure_from_ru(en, ru)
    assert "# Title {#t}" in fixed
    assert "### Signature" in fixed
    assert "MIN(T)->T?" in fixed


def test_restore_explicit_heading_anchors_overwrites_mismatched_en_id():
    """REQUIREMENTS §8: Cyrillic RU id maps to English, not copied verbatim."""
    ru = "### Описание полей в ответе {#fields-Описание}\n\nBody.\n"
    en = "### Response field descriptions {#fields-Wrong}\n\nBody.\n"
    fixed = restore_explicit_heading_anchors(en, ru)
    assert "{#fields-Response}" in fixed
    assert "{#fields-Описание}" not in fixed
    assert "{#fields-Wrong}" not in fixed


def test_restore_explicit_heading_anchors_empty_or_already_present():
    assert restore_explicit_heading_anchors("", "# A {#a}\n") == ""
    assert restore_explicit_heading_anchors("# A {#a}\n", "") == "# A {#a}\n"
    ru = "# Приложение на C# {#csharp-app}\n\nBody.\n"
    en = "# Example app in C# (.NET) {#csharp-app}\n\nBody.\n"
    assert restore_explicit_heading_anchors(en, ru) == en


def test_restore_explicit_heading_anchors_skips_level_mismatch():
    ru = "# Приложение {#csharp-app}\n\n## Nested\n"
    en = "## Example app in C# (.NET)\n\n# Nested\n"
    assert "{#csharp-app}" not in restore_explicit_heading_anchors(en, ru)


def test_sync_missing_signature_skips_when_en_already_has_block():
    ru = (
        "## COUNT {#count}\n\n"
        "### Сигнатура\n\n"
        "```yql\nCOUNT(*)->Uint64\n```\n\n"
        "Подсчет.\n"
    )
    en = (
        "## COUNT {#count}\n\n"
        "### Signature\n\n"
        "```yql\nCOUNT(*)->Uint64\n```\n\n"
        "Count rows.\n"
    )
    assert sync_missing_signature_sections(en, ru) == en


def test_sync_missing_signature_skips_section_without_en_twin():
    ru = (
        "## COUNT {#count}\n\n"
        "### Сигнатура\n\n"
        "```yql\nCOUNT(*)->Uint64\n```\n\n"
        "Подсчет.\n"
    )
    en = "## SUM {#sum}\n\nSum.\n"
    assert "### Signature" not in sync_missing_signature_sections(en, ru)
    assert "COUNT(*)->Uint64" not in sync_missing_signature_sections(en, ru)


def test_sync_missing_signature_empty_inputs():
    assert sync_missing_signature_sections("", "## A {#a}\n") == ""
    assert sync_missing_signature_sections("## A {#a}\n", "") == "## A {#a}\n"
