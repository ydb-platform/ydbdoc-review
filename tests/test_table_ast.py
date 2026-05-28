"""Tests for strict markdown table AST translation helpers."""

from ydbdoc_review.document_mask import MaskRegistry
from ydbdoc_review.table_ast import build_table_row_plan, render_table_row_plan


def test_build_row_plan_masks_html_and_links_but_exposes_labels():
    row = "| `--input-batch` | <ul><li>Пакетирование [выключено](#x).</li></ul> |"
    reg = MaskRegistry()
    built = build_table_row_plan(
        row,
        line_no=3,
        registry=reg,
        source_is_russian=True,
    )
    assert built is not None
    _plan, units = built
    assert units
    assert any("выключено" in u.source_line for u in units)
    assert all("<ul>" not in u.source_line for u in units)
    assert all("](#x)" not in u.source_line for u in units)


def test_render_row_plan_preserves_structure_and_blocks_pipe_injection():
    row = "| `--input-batch` | <ul><li>Пакетирование [выключено](#x).</li></ul> |"
    reg = MaskRegistry()
    built = build_table_row_plan(
        row,
        line_no=3,
        registry=reg,
        source_is_russian=True,
    )
    assert built is not None
    plan, units = built
    translations = {u.unit_id: u.source_line.replace("Пакетирование", "Batching") for u in units}
    # Simulate model trying to break a cell by injecting a table delimiter.
    first_uid = units[0].unit_id
    translations[first_uid] = translations[first_uid] + " | BREAK"
    out = render_table_row_plan(plan, translations=translations, registry=reg)
    assert out.count("|") == row.count("|")
    assert "<ul><li>" in out
    assert "](#x)" in out

