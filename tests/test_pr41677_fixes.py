"""Regression tests for PR #41677 translation defects."""

from ydbdoc_review.ru_en_sync import finalize_en_document_from_ru, sync_manual_tabs_fences_from_source
from ydbdoc_review.table_ast import repair_table_rows_from_ru
from ydbdoc_review.tabs_repair import repair_tab_labels_from_source
from ydbdoc_review.translate_postprocess import (
    apply_en_postprocess_from_ru,
    fix_en_heading_lines,
    fix_heading_structure_from_ru,
    fix_ru_link_labels_in_en,
    fix_space_before_markdown_link,
    fix_spurious_backtick_padding,
    normalize_en_spacing_after_slots,
    strip_stray_heading_anchors_in_prose,
)


def test_normalize_does_not_break_heading_backticks():
    en = "## Single query execution ` stdin ` {#one-request}"
    out = normalize_en_spacing_after_slots(en)
    assert "` stdin `" in out or "`stdin`" in out
    fixed = fix_spurious_backtick_padding(out)
    fixed = fix_en_heading_lines(fixed)
    assert "`stdin`" not in fixed or "stdin" not in fixed.split("{#")[0]
    assert "{#one-request}" in fixed


def test_fix_spurious_backtick_padding():
    assert fix_spurious_backtick_padding("| ` --input-batch ` |") == "| `--input-batch` |"
    assert "stdin `or` stdout" in fix_spurious_backtick_padding("stdin`or`stdout")


def test_strip_stray_anchor_in_bullet():
    en = "protection against ](url)SQL injections{#one-request}."
    out = strip_stray_heading_anchors_in_prose(en)
    assert "{#one-request}" not in out


def test_repair_table_header_extra_column():
    ru = "| Имя | Описание |\n| --- | --- |\n"
    en = "|Name|Description|[выключено](#x)|\n| --- | --- |\n"
    out = repair_table_rows_from_ru(ru, en)
    assert out.count("|") == ru.count("|")
    assert "выключено" not in out


def test_dedup_possible_values_in_cell():
    ru = "| x | a |\n"
    cell = (
        "Possible values:<br/><ul><li>`a`</li></ul>"
        "<br/>Possible values:<br/><ul><li>`a`</li></ul>"
        "<br/>Possible values:<br/><ul><li>`a`</li></ul>"
    )
    en = f"| x | {cell} |\n"
    out = repair_table_rows_from_ru(ru, en)
    assert out.count("Possible values:") == 1


def test_fix_ru_link_labels_and_space_before_link():
    en = "Batching is[выключено](#x)."
    out = fix_space_before_markdown_link(fix_ru_link_labels_in_en(en))
    assert "is [disabled]" in out


def test_repair_tab_labels_removes_extra_tsv():
    ru = (
        "{% list tabs %}\n\n"
        "- JSON\n\n"
        "  ```json\n"
        "  {}\n"
        "  ```\n\n"
        "- CSV\n\n"
        "  text\n\n"
        "{% endlist %}\n"
    )
    en = (
        "{% list tabs %}\n\n"
        "- JSON\n\n"
        "  ```json\n"
        "  {}\n"
        "  ```\n\n"
        "- CSV\n\n"
        "  text\n\n"
        "- TSV\n\n"
        "  extra\n\n"
        "{% endlist %}\n"
    )
    out, changed = repair_tab_labels_from_source(ru, en)
    assert changed
    assert "- TSV" not in out


def test_sync_manual_tabs_fences_restores_bash():
    ru = (
        "{% list tabs %}\n\n"
        "- CSV\n\n"
        "  ```bash\n"
        "  echo hi\n"
        "  ```\n\n"
        "{% endlist %}\n"
    )
    en = (
        "{% list tabs %}\n\n"
        "- CSV\n\n"
        "  ```\n"
        "  echo hi\n"
        "  ```\n\n"
        "{% endlist %}\n"
    )
    out, changed = sync_manual_tabs_fences_from_source(ru, en)
    assert changed
    assert "```bash" in out


def test_fix_heading_structure_splits_glued_title():
    ru = "## Единичное исполнение запроса {#one-request}\n\nЭта команда поддерживает передачу параметров.\n"
    en = "## Single query execution This command supports passing parameters via stdin. {#one-request}\n"
    out = fix_heading_structure_from_ru(ru, en)
    lines = out.splitlines()
    assert lines[0].endswith("{#one-request}")
    assert len(lines) >= 2
    assert "stdin" in lines[1]


def test_apply_postprocess_parameterized_style():
    ru = "## Единичное исполнение запроса {#one-request}\n| Имя | Описание |\n"
    en = "## Single query execution ` stdin ` {#one-request}\n|Name|Description|[выключено](#x)|\n"
    out = apply_en_postprocess_from_ru(ru, en)
    assert "[выключено]" not in out
    assert "` stdin `" not in out
