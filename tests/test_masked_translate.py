"""Tests for mask → translate → unmask pipeline."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.annotated_translate import refine_tab_regions
from ydbdoc_review.document_mask import MaskRegistry
from ydbdoc_review.document_structure import analyze_document_structure
from ydbdoc_review.file_translate import translate_text_with_plan
from ydbdoc_review.masked_chunking import chunk_masked_text
from ydbdoc_review.masked_translate import (
    build_masked_segments,
    translate_masked_segment,
    translate_with_mask,
)

FIXTURE_RU = (
    Path(__file__).resolve().parent / "fixtures" / "placeholder_fence_sample.md"
)


@pytest.fixture
def fence_sample_ru() -> str:
    return FIXTURE_RU.read_text(encoding="utf-8")


def _mock_masked_chunk(_s, masked: str, **_k) -> str:
    """Translate Cyrillic prose; keep placeholders verbatim."""
    out = masked
    for ru, en in (
        ("Вступление", "Intro"),
        ("Заключение", "Outro"),
        ("Вручную", "Manually"),
        ("С использованием systemd", "Using systemd"),
        ("Запустите", "Start"),
        ("сервис", "the service"),
    ):
        out = out.replace(ru, en)
    return out


def test_chunk_does_not_split_placeholder():
    ph = "⟦LINK:1⟧"
    text = "x " * 100 + ph + " y " * 100
    chunks = chunk_masked_text(text, max_chars=120)
    for ch in chunks:
        assert ch.count("⟦") == ch.count("⟧")


def test_config_tabs_copy_without_llm(fence_sample_ru: str):
    ru = fence_sample_ru
    with patch(
        "ydbdoc_review.masked_translate.translate_masked_chunk",
        side_effect=_mock_masked_chunk,
    ) as mocked:
        en, llm = translate_with_mask(
            MagicMock(),
            source_path="sample.md",
            source_text=ru,
            source_lang="Russian",
            target_lang="English",
        )
    assert mocked.call_count >= 1
    assert en.count("```") == ru.count("```")
    assert "Intro" in en or "EN" in en.lower() or "Manually" in en


def test_translate_text_with_plan_default_is_masked():
    ru = "### Заголовок {#t}\n\nПривет [мир](x.md).\n"
    with patch(
        "ydbdoc_review.masked_translate.translate_masked_chunk",
        side_effect=lambda _s, masked, **_k: masked.replace("Привет", "Hello"),
    ):
        out, n = translate_text_with_plan(
            MagicMock(),
            source_path="x.md",
            source_text=ru,
            source_lang="Russian",
            target_lang="English",
        )
    assert n >= 1
    assert "Hello" in out
    assert "[мир](x.md)" in out
    assert "Привет" not in out


def test_build_masked_segments_preserves_links():
    line = "Текст [узла](a.md) и [кластера](b.md)."
    ru = line + "\n"
    regions = analyze_document_structure(ru, source_is_russian=True)
    reg = MaskRegistry()
    segs = build_masked_segments(ru, regions, reg, source_is_russian=True)
    tr = [s for s in segs if s.kind == "translate"][0]
    assert "⟦LINK:" in tr.masked_text
    assert len(reg.atoms) >= 2


def test_translate_table_segment_uses_line_json():
    ru = (
        "| Name | Description |\n"
        "| --- | --- |\n"
        "| `--input-batch` | Пакетирование [выключено](#x). |\n"
    )
    regions = analyze_document_structure(ru, source_is_russian=True)
    reg = MaskRegistry()
    segs = build_masked_segments(ru, regions, reg, source_is_russian=True)
    seg = [s for s in segs if s.kind == "translate"][0]
    assert seg.action == "translate_table"
    def fake_line_json(_settings, units, **_kwargs):
        out: dict[str, str] = {}
        for u in units:
            out[u.unit_id] = u.source_line.replace("Пакетирование", "Batching")
        return out

    with patch(
        "ydbdoc_review.masked_translate.translate_line_units",
        side_effect=fake_line_json,
    ) as mocked_lines, patch(
        "ydbdoc_review.masked_translate.translate_masked_chunk",
        side_effect=AssertionError("masked LLM path should not be used for table segments"),
    ):
        out, llm = translate_masked_segment(
            MagicMock(),
            seg,
            reg,
            source_lang="Russian",
            target_lang="English",
            source_path="x.md",
            source_is_russian=True,
        )
    mocked_lines.assert_called_once()
    assert llm == 1
    assert out.count("|") >= 6
    assert "Batching" in out


def test_translate_table_segment_masks_html_inside_cells():
    ru = (
        "| Name | Description |\n"
        "| --- | --- |\n"
        "| `--input-batch` | <ul><li>Пакетирование [выключено](#x).</li></ul> |\n"
    )
    regions = analyze_document_structure(ru, source_is_russian=True)
    reg = MaskRegistry()
    seg = [s for s in build_masked_segments(ru, regions, reg, source_is_russian=True) if s.kind == "translate"][0]

    def fake_line_json(_settings, units, **_kwargs):
        assert units, "expected table cell units"
        # We should send masked cell text, not raw <ul>/<li>.
        assert any("⟦HTML:" in u.source_line for u in units)
        out: dict[str, str] = {}
        for u in units:
            out[u.unit_id] = u.source_line.replace("Пакетирование", "Batching")
        return out

    with patch(
        "ydbdoc_review.masked_translate.translate_line_units",
        side_effect=fake_line_json,
    ):
        out, llm = translate_masked_segment(
            MagicMock(),
            seg,
            reg,
            source_lang="Russian",
            target_lang="English",
            source_path="x.md",
            source_is_russian=True,
        )
    assert llm == 1
    assert "<ul><li>" in out
    assert "Batching" in out
