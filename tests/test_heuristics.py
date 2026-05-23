"""Deterministic heuristics from prompts/09_quality_heuristics.md."""

from types import SimpleNamespace

from ydbdoc_review.heuristics import (
    _check_broken_markdown_link,
    _check_cyrillic_in_en,
    _check_fence_unbalanced,
    _check_file_length_mismatch,
    _check_heading_anchor_mismatch,
    _check_heading_count_mismatch,
    _check_liquid_tags_balance,
    _check_list_tabs_mismatch,
    _check_table_checkmark_drift,
    _check_wikipedia_ru_in_en,
    load_rules,
    render_findings_markdown,
    run_heuristics,
)


def _settings():
    return SimpleNamespace(
        model_translate="yandexgpt-5.1",
        model_translation_verify="qwen3.6-35b-a3b",
        prompts_dir="prompts",
    )


def test_load_rules_reads_prompt_09():
    rules = load_rules("prompts")
    names = {r.name for r in rules}
    assert "cyrillic_in_en" in names
    assert "file_length_mismatch" in names
    assert "section_untranslated" in names
    assert "wikipedia_ru_in_en" in names
    assert "broken_markdown_link" in names


def test_cyrillic_in_en_flags_russian_letter():
    f = _check_cyrillic_in_en(source="...", translation="Install Привет via pip\n")
    assert f is not None
    assert f.rule == "cyrillic_in_en"
    assert f.severity == "warning"


def test_cyrillic_in_en_clean():
    assert _check_cyrillic_in_en(source="...", translation="Install via pip\n") is None


def test_file_length_mismatch_critical():
    ru = "Длинный русский текст. " * 100
    en = "Short.\n"
    f = _check_file_length_mismatch(source=ru, translation=en)
    assert f is not None
    assert f.severity == "critical"


def test_file_length_mismatch_close_enough():
    ru = "Hello\n" * 50
    en = "Hello\n" * 47
    assert _check_file_length_mismatch(source=ru, translation=en) is None


def test_heading_count_mismatch():
    ru = "## a\n\n## b\n\n### sub\n"
    en = "## a\n\n### sub\n"  # lost one ##
    f = _check_heading_count_mismatch(source=ru, translation=en)
    assert f is not None and f.severity == "critical"


def test_fence_unbalanced_detected():
    en = "```text\nhello\n"  # opening only
    f = _check_fence_unbalanced(source="", translation=en)
    assert f is not None and f.severity == "critical"


def test_list_tabs_mismatch():
    ru = "{% list tabs %}\n- Go\n{% endlist %}\n"
    en = ""
    f = _check_list_tabs_mismatch(source=ru, translation=en)
    assert f is not None and f.severity == "critical"


def test_liquid_tags_balance_imbalanced():
    en = "{% note info %}\nbody\n"  # missing endnote
    f = _check_liquid_tags_balance(source="", translation=en)
    assert f is not None and f.severity == "critical"


def test_run_heuristics_skips_llm_when_no_unknown_rules(monkeypatch):
    """LLM-only rules (section_untranslated) should be attempted; we stub them out."""
    monkeypatch.setattr(
        "ydbdoc_review.heuristics._run_llm_rules",
        lambda *a, **k: [],
    )
    findings = run_heuristics(
        _settings(),
        source="## a\n\n## b\n",
        translation="## a\n\n## b\n",
        source_lang="Russian",
        target_lang="English",
        file_label="test.md",
    )
    # Both files balanced and same; deterministic checks pass; LLM stubbed.
    assert findings == []


def test_render_findings_markdown_empty():
    assert render_findings_markdown([]) == "_Без замечаний._"


def test_wikipedia_ru_in_en_critical():
    f = _check_wikipedia_ru_in_en(
        source="x",
        translation="[Snappy](https://ru.wikipedia.org/wiki/Snappy_(библиотека))",
    )
    assert f is not None and f.severity == "critical"


def test_broken_markdown_link_bare_url():
    f = _check_broken_markdown_link(
        source="x",
        translation="see (https://example.com) for details",
    )
    assert f is not None and f.rule == "broken_markdown_link"


def test_heading_anchor_mismatch():
    ru = "### Формат a {#a}\n### Формат b {#b}\n"
    en = "### Format a {#a}\n### Format b {#wrong}\n"
    f = _check_heading_anchor_mismatch(source=ru, translation=en)
    assert f is not None and "b→wrong" in f.detail


def test_table_checkmark_drift():
    ru = "|`DyNumber`| | | |✓| |\n"
    en = "|`DyNumber`| | |✓| | |\n"
    f = _check_table_checkmark_drift(source=ru, translation=en)
    assert f is None  # no header row → skip


def test_table_checkmark_drift_with_headers():
    from ydbdoc_review.heuristics import _parse_checkmark_tables

    ru = (
        "|Type|csv|json_list|\n"
        "|---|---|\n"
        "|`Uuid`|✓| |\n"
        "|Type|csv|json_list|\n"
        "|---|---|\n"
        "|`Uuid`|✓|✓|\n"
    )
    en = (
        "|Type|csv|json_list|\n"
        "|---|---|\n"
        "|`Uuid`|✓| |\n"
        "|Type|csv|json_list|\n"
        "|---|---|\n"
        "|`Uuid`|✓|✓|\n"
    )
    assert len(_parse_checkmark_tables(ru)) == 2
    assert _check_table_checkmark_drift(source=ru, translation=en) is None


def test_table_checkmark_drift_detail_message():
    ru = (
        "|Type|csv|parquet|\n"
        "|---|---|\n"
        "|`DyNumber`| |✓|\n"
    )
    en = (
        "|Type|csv|parquet|\n"
        "|---|---|\n"
        "|`DyNumber`|✓|✓|\n"
    )
    f = _check_table_checkmark_drift(source=ru, translation=en)
    assert f is not None
    assert "DyNumber" in f.detail
    assert "csv" in f.detail
    assert "SOURCE=" in f.detail
