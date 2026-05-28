"""Regression tests for PR #41667 translation defects."""

from ydbdoc_review.document_mask import MaskRegistry, mask_links_split_label, mask_translatable_text
from ydbdoc_review.ru_en_sync import (
    finalize_en_document_from_ru,
    repair_table_separator_rows_from_ru,
    sync_fenced_blocks_from_source,
)
from ydbdoc_review.translate_postprocess import (
    apply_en_postprocess_from_ru,
    fix_en_heading_lines,
    normalize_en_spacing_after_slots,
)


def test_mask_links_split_label_exposes_cyrillic_label():
    reg = MaskRegistry()
    masked = mask_links_split_label("[командой YQL](x.md)", reg)
    assert "командой" in masked
    assert "x.md" not in masked
    assert "LINK_OPEN" in masked
    assert "LINK_CLOSE" in masked


def test_prose_masking_splits_link_labels():
    reg = MaskRegistry()
    masked = mask_translatable_text("Use [LRU-кеше](cache.md) here.", reg)
    assert "LRU-кеше" in masked
    assert "cache.md" not in masked.replace("LINK_CLOSE", "")


def test_sync_fenced_blocks_when_inner_lines_missing():
    ru = "```json\n{\"sum1\":115}\nline2\n```\n"
    en = "```json\nline2\n```\n"
    out, changed = sync_fenced_blocks_from_source(ru, en)
    assert changed
    assert '{"sum1":115}' in out


def test_repair_table_separator_row_from_ru():
    ru = "| --- | --- |\n"
    en = "| ---|--- |[выключено](#x) |\n"
    out = repair_table_separator_rows_from_ru(ru, en)
    assert out.strip() == ru.strip()


def test_fix_en_heading_strips_links_and_duplicate_anchors():
    en = "## See also [docs](x.md) {#see-also} {#see-also}\n"
    out = fix_en_heading_lines(en)
    assert "[docs]" not in out
    assert out.count("{#see-also}") == 1


def test_normalize_en_spacing_macros_only():
    assert normalize_en_spacing_after_slots("{{ ydb-short-name }}The") == (
        "{{ ydb-short-name }} The"
    )
    assert normalize_en_spacing_after_slots("stdin`or`stdout") == "stdin`or`stdout"


def test_fix_spurious_backtick_handles_or():
    from ydbdoc_review.translate_postprocess import fix_spurious_backtick_padding

    assert "stdin `or` stdout" in fix_spurious_backtick_padding("stdin`or`stdout")


def test_finalize_separator_and_fence():
    ru = (
        "| A | B |\n"
        "| --- | --- |\n"
        "```bash\necho 1\n```\n"
    )
    en = (
        "| A | B |\n"
        "| ---|--- |junk |\n"
        "```bash\n```\n"
    )
    out = finalize_en_document_from_ru(ru, en)
    assert "| --- | --- |" in out
    assert "echo 1" in out


def test_apply_postprocess_heading_and_spacing():
    ru = "### См. также {#see-also}\n"
    en = "### See also [link](x.md) {#see-also} {#see-also}\n"
    out = apply_en_postprocess_from_ru(ru, en)
    assert "[link]" not in out
    assert out.count("{#see-also}") == 1
