"""Tests for Cyrillic homoglyph cleanup in EN output."""

from __future__ import annotations

from ydbdoc_review.validation.homoglyphs import fix_cyrillic_homoglyphs_in_en


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
