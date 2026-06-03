"""Tests for Cyrillic homoglyph cleanup in EN output."""

from __future__ import annotations

from ydbdoc_review.validation.homoglyphs import (
    fix_cyrillic_homoglyphs_in_en,
    fix_russian_angle_placeholders_in_en_fences,
    postprocess_en_target_markdown,
)


def test_fix_fqdn_vm_comment():
    line = "    - host: static-node-1.ydb-cluster.com #FQDN ВМ\n"
    fixed = fix_cyrillic_homoglyphs_in_en(line)
    assert "#FQDN VM" in fixed
    assert "ВМ" not in fixed


def test_leaves_russian_prose_untouched():
    text = "![Ручная установка, запущенные статические узлы](path.png)\n"
    assert fix_cyrillic_homoglyphs_in_en(text) == text


def test_fix_node_id_comment():
    line = "      - node_id: static-node-1.ydb-cluster.com #FQDN ВМ\n"
    fixed = fix_cyrillic_homoglyphs_in_en(line)
    assert "VM" in fixed


def test_fix_stroka_in_fenced_block():
    text = "```bash\nydb admin cluster bootstrap --uuid <строка>\n```\n"
    fixed = fix_russian_angle_placeholders_in_en_fences(text)
    assert "<string>" in fixed
    assert "<строка>" not in fixed


def test_postprocess_leaves_prose_cyrillic():
    text = "Простой текст с <строка> вне fence.\n"
    assert postprocess_en_target_markdown(text) == text
