"""QA chunks, tab labels, CLI flag fixes."""

from ydbdoc_review.document_segments import parse_document_units
from ydbdoc_review.heuristics import _check_tab_labels_parity
from ydbdoc_review.tabs_repair import config_tab_label_lines
from ydbdoc_review.qa_chunks import build_qa_chunks, merge_chunk_reports, needs_qa_chunking
from ydbdoc_review.tabs_repair import is_tab_label_line, repair_tab_labels_from_source
from ydbdoc_review.translate_postprocess import fix_dashed_cli_flags


def test_tab_labels_parity_ignores_manual_tabs_cyrillic_vs_english():
    ru = (
        "{% list tabs %}\n"
        "- mirror-3-dc-3nodes\n"
        "```yaml\n"
        "x: 1\n"
        "```\n"
        "{% endlist %}\n\n"
        "{% list tabs %}\n"
        "- Вручную\n"
        "Text\n"
        "{% endlist %}\n"
    )
    en = (
        "{% list tabs %}\n"
        "- mirror-3-dc-3nodes\n"
        "```yaml\n"
        "x: 1\n"
        "```\n"
        "{% endlist %}\n\n"
        "{% list tabs %}\n"
        "- Manually\n"
        "Text\n"
        "{% endlist %}\n"
    )
    assert config_tab_label_lines(ru) == config_tab_label_lines(en)
    assert _check_tab_labels_parity(source=ru, translation=en) is None


def test_tab_label_line_detected():
    assert is_tab_label_line("- mirror-3-dc-3nodes")
    assert not is_tab_label_line("- Use the mirror scheme")


def test_repair_missing_first_tab_label():
    ru = (
        "{% list tabs %}\n"
        "- mirror-3-dc-3nodes\n"
        "```yaml\n"
        "a: 1\n"
        "```\n"
        "- mirror-3-dc-9nodes\n"
        "```yaml\n"
        "b: 2\n"
        "```\n"
        "{% endlist %}\n"
    )
    en = (
        "{% list tabs %}\n"
        "```yaml\n"
        "a: 1\n"
        "```\n"
        "- mirror-3-dc-9nodes\n"
        "```yaml\n"
        "b: 2\n"
        "```\n"
        "{% endlist %}\n"
    )
    fixed, applied = repair_tab_labels_from_source(ru, en)
    assert applied
    assert "- mirror-3-dc-3nodes" in fixed
    assert _check_tab_labels_parity(source=ru, translation=fixed) is None


def test_fix_dashed_cli_flags_uuid():
    en = "ydb admin cluster bootstrap -- uuid <string>\n"
    out = fix_dashed_cli_flags(en)
    assert "--uuid <string>" in out


def test_qa_chunks_overlap_units(monkeypatch):
    monkeypatch.setenv("YDBDOC_QA_CHUNK_MAX_CHARS", "500")
    monkeypatch.setenv("YDBDOC_QA_CHUNK_OVERLAP_UNITS", "1")
    unit = "line of text.\n" * 40
    ru = "\n".join(f"### S{i}\n{unit}" for i in range(12))
    en = ru.replace("line of text", "line of EN text")
    chunks = build_qa_chunks(ru, en, doc_label="big.md")
    assert len(chunks) >= 2
    assert chunks[1].overlap_units >= 1


def test_needs_qa_chunking_large_pair():
    assert needs_qa_chunking("x" * 30_000, "y" * 30_000)


def test_merge_chunk_reports_worst_verdict():
    accept = "### Вердикт\n**ПРИНИМАТЬ**\n\n### Блокеры\n_Нет._\n\n### Оговорки\n_Нет._\n\n### Кратко\nok\n"
    reject = (
        "### Вердикт\n**НЕ ПРИНИМАТЬ**\n\n### Блокеры\n- x\n\n"
        "### Оговорки\n_Нет._\n\n### Кратко\nbad\n"
    )
    merged = merge_chunk_reports(
        [accept, reject],
        file_label="f.md",
        chunk_labels=["a", "b"],
    )
    assert "НЕ ПРИНИМАТЬ" in merged
    assert "2 пересекающимся чанкам" in merged


def test_list_tabs_unit_parsed():
    text = "{% list tabs %}\n- OSS\n\nText\n{% endlist %}\n"
    kinds = [u.kind for u in parse_document_units(text)]
    assert "tabs" in kinds
