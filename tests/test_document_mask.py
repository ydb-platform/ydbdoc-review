"""Tests for inline mask / unmask."""

from ydbdoc_review.document_mask import (
    MaskRegistry,
    has_broken_placeholder_tokens,
    mask_translatable_text,
    placeholder_sequence_matches,
    placeholder_key_sequence,
    restore_missing_placeholders,
    unmask_text,
    validate_placeholders,
)


def test_mask_two_links_in_one_line():
    line = (
        "См. [узла](a.md) и [кластера](b.md) для деталей."
    )
    reg = MaskRegistry()
    masked = mask_translatable_text(line, reg)
    keys = placeholder_key_sequence(masked)
    assert len(keys) == 2
    assert keys[0].startswith("LINK:")
    assert keys[1].startswith("LINK:")
    assert "⟦LINK:1⟧" in masked
    assert "⟦LINK:2⟧" in masked
    assert "узла" not in masked
    restored = unmask_text(masked, reg)
    assert restored == line


def test_mask_html_table_cell():
    row = '| <ul><li>Элемент</li></ul> | `id` |'
    reg = MaskRegistry()
    masked = mask_translatable_text(row, reg)
    assert "HTML" in "".join(reg.atoms)
    assert "CODE" in "".join(reg.atoms)
    assert unmask_text(masked, reg) == row


def test_restore_missing_placeholder():
    src = "Текст ⟦LINK:1⟧ конец."
    bad = "Text end."
    fixed = restore_missing_placeholders(src, bad)
    assert "⟦LINK:1⟧" in fixed


def test_validate_placeholders():
    src = "A ⟦LINK:1⟧ B ⟦VAR:1⟧"
    ok = "X ⟦LINK:1⟧ Y ⟦VAR:1⟧"
    assert validate_placeholders(src, ok) == []
    assert validate_placeholders(src, "X Y") == ["LINK:1", "VAR:1"]


def test_placeholder_sequence_and_broken_detection():
    src = "A ⟦LINK:1⟧ B ⟦VAR:1⟧"
    good = "X ⟦LINK:1⟧ Y ⟦VAR:1⟧"
    bad_order = "X ⟦VAR:1⟧ Y ⟦LINK:1⟧"
    broken = "X ⟦LINK:1 Y"
    assert placeholder_sequence_matches(src, good)
    assert not placeholder_sequence_matches(src, bad_order)
    assert has_broken_placeholder_tokens(broken)
