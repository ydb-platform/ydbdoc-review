"""Tests for Cyrillic homoglyph cleanup in EN output."""

from __future__ import annotations

from ydbdoc_review.validation.homoglyphs import (
    fix_cyrillic_homoglyphs_in_en,
    fix_russian_angle_placeholders_in_en_fences,
    normalize_confusable_cyrillic,
    postprocess_en_target_markdown,
)


def test_normalize_confusable_cyrillic_cpp_tab():
    assert normalize_confusable_cyrillic("С++") == "C++"


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


def test_fix_stroka_in_indented_fenced_block():
    """Fences inside numbered lists are indented (initial-deployment.md pattern)."""
    text = (
        "5. The cluster initialization command is executed in the following form:\n"
        "\n"
        "   ```bash\n"
        "   export LD_LIBRARY_PATH=/opt/ydb/lib\n"
        "   ydb admin cluster bootstrap --uuid <строка>\n"
        "   echo $?\n"
        "   ```\n"
    )
    fixed = fix_russian_angle_placeholders_in_en_fences(text)
    assert "bootstrap --uuid <string>" in fixed
    assert "<строка>" not in fixed
    assert "   ```bash" in fixed


def test_postprocess_fixes_indented_fence_stroka():
    text = (
        "Prose line.\n"
        "\n"
        "   ```bash\n"
        "   ydb admin cluster bootstrap --uuid <строка>\n"
        "   ```\n"
    )
    fixed = postprocess_en_target_markdown(text)
    assert "<string>" in fixed
    assert "<строка>" not in fixed


def test_postprocess_fixes_angle_placeholder_in_prose_backticks():
    text = "   - `Restore from '<путь>' completed successfully`\n"
    fixed = postprocess_en_target_markdown(text)
    assert "<path>" in fixed
    assert "путь" not in fixed


def test_postprocess_fixes_multiline_error_placeholder():
    text = "   - `Restore from '<путь>' failed: <описание ошибки>`\n"
    fixed = postprocess_en_target_markdown(text)
    assert "<path>" in fixed
    assert "<error description>" in fixed
    assert "путь" not in fixed


def test_postprocess_inserts_blank_after_fence():
    text = "  ```\n- item\n"
    fixed = postprocess_en_target_markdown(text)
    lines = fixed.splitlines()
    assert lines[1] == ""
    assert lines[2] == "- item"
