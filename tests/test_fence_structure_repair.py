"""Structure-only fence repair and EN leak fixes."""

from ydbdoc_review.fence_repair import repair_fences_from_source
from ydbdoc_review.heuristics import _check_fence_code_line_parity
from ydbdoc_review.translate_postprocess import (
    fix_common_ru_leaks_in_en,
    fix_dashed_cli_flags,
)


def test_repair_fences_inserts_closing_without_ru_body():
    ru = "Before\n```yaml\na: 1\n```\nAfter\n"
    en = "Before\n```yaml\na: 1\nAfter\n"
    out, applied = repair_fences_from_source(ru, en)
    assert applied
    assert "a: 1\n```" in out
    assert "After" in out
    assert out.count("```") == 2


def test_repair_fences_preserves_en_translated_inner():
    ru = "```bash\nydb admin cluster bootstrap --uuid <строка>\n```\n"
    en = "```bash\nydb admin cluster bootstrap -- uuid <string>\n"
    out, applied = repair_fences_from_source(ru, en)
    assert applied
    assert "<string>" in out
    assert "<строка>" not in out
    assert "-- uuid" in out or "--uuid" in out


def test_fix_common_ru_leaks_in_en():
    text = "host #FQDN ВМ\nbootstrap --uuid <строка>\n"
    out = fix_common_ru_leaks_in_en(text)
    assert "#VM FQDN" in out
    assert "<string>" in out
    assert "ВМ" not in out


def test_fence_code_line_parity_ignores_string_placeholder():
    ru = "```\nydb admin cluster bootstrap --uuid <строка>\n```\n"
    en = "```\nydb admin cluster bootstrap --uuid <string>\n```\n"
    assert _check_fence_code_line_parity(source=ru, translation=en) is None


def test_fix_dashed_cli_flags_before_parity():
    en = "```\nydb admin cluster bootstrap -- uuid <string>\n```\n"
    fixed = fix_dashed_cli_flags(en)
    ru = "```\nydb admin cluster bootstrap --uuid <строка>\n```\n"
    assert _check_fence_code_line_parity(source=ru, translation=fixed) is None
