"""Tests for placeholder (JSON line) translation pipeline."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ydbdoc_review.annotated_translate import refine_tab_regions
from ydbdoc_review.document_structure import analyze_document_structure
from ydbdoc_review.file_translate import translate_text_with_plan
from ydbdoc_review.placeholder_translate import (
    LineUnit,
    _parse_batch_response,
    assemble_translate_segment,
    build_placeholder_segments,
    line_needs_translation,
    translate_with_placeholders,
)

FIXTURE_RU = (
    Path(__file__).resolve().parent / "fixtures" / "placeholder_fence_sample.md"
)
V1_RU = Path(__file__).resolve().parents[1] / "debug/v1-annotated-dump/00_source_ru.md"


@pytest.fixture
def fence_sample_ru() -> str:
    return FIXTURE_RU.read_text(encoding="utf-8")


def _mock_translate_units(units: list[LineUnit], **_kwargs) -> dict[str, str]:
    """Prefix EN: for each line; keeps structure (fences never appear in units)."""
    out: dict[str, str] = {}
    for u in units:
        line = u.source_line
        if line.strip().startswith("- "):
            out[u.unit_id] = line.replace(
                "Вручную", "Manually"
            ).replace("С использованием systemd", "Using systemd")
        elif "Вступление" in line:
            out[u.unit_id] = "Intro EN."
        elif "Заключение" in line:
            out[u.unit_id] = "Outro EN."
        elif "Запустите" in line:
            out[u.unit_id] = "Start the service."
        else:
            body = line.strip()
            if body and not body.startswith("{%"):
                indent = line[: len(line) - len(line.lstrip())]
                out[u.unit_id] = indent + "EN: " + body
            else:
                out[u.unit_id] = line
    return out


def test_line_needs_translation_tab_label_only():
    region = analyze_document_structure(
        "- Вручную\n", source_is_russian=True
    )[0]
    assert line_needs_translation(
        "- Вручную", region=region, line_no=1, source_is_russian=True
    )
    assert not line_needs_translation(
        "  ```bash", region=region, line_no=1, source_is_russian=True
    )


def test_config_tabs_are_copy_segments():
    ru = (
        "Вступление.\n\n"
        "{% list tabs %}\n\n"
        "- mirror-3-dc-3nodes\n\n"
        "  ```yaml\n"
        "  x: 1\n"
        "  ```\n\n"
        "{% endlist %}\n\n"
        "Заключение.\n"
    )
    regions = refine_tab_regions(
        ru, analyze_document_structure(ru, source_is_russian=True)
    )
    segs = build_placeholder_segments(ru, regions, source_is_russian=True)
    copy_bodies = [s.text for s in segs if s.kind == "copy"]
    assert any("```yaml" in b for b in copy_bodies)
    assert all("mirror-3-dc-3nodes" in b for b in copy_bodies if "mirror" in b)


def test_translate_with_placeholders_preserves_all_fences(fence_sample_ru: str):
    ru = fence_sample_ru
    with patch(
        "ydbdoc_review.placeholder_translate.translate_line_units",
        side_effect=lambda _s, units, **_k: _mock_translate_units(units),
    ):
        en, llm = translate_with_placeholders(
            MagicMock(),
            source_path="sample.md",
            source_text=ru,
            source_lang="Russian",
            target_lang="English",
        )
    assert ru.count("```") == en.count("```")
    assert ru.count("```bash") == en.count("```bash")
    assert ru.count("```yaml") == en.count("```yaml")
    assert "- mirror-3-dc-3nodes" in en
    assert "sudo su - ydb" in en
    assert llm >= 1


@pytest.mark.skipif(not V1_RU.is_file(), reason="optional debug/v1-annotated-dump/00_source_ru.md")
def test_translate_full_v1_debug_fixture_preserves_fences():
    ru = V1_RU.read_text(encoding="utf-8")
    with patch(
        "ydbdoc_review.placeholder_translate.translate_line_units",
        side_effect=lambda _s, units, **_k: _mock_translate_units(units),
    ):
        en, _ = translate_with_placeholders(
            MagicMock(),
            source_path="deployment-configuration-v1.md",
            source_text=ru,
            source_lang="Russian",
            target_lang="English",
        )
    assert ru.count("```") == en.count("```")
    assert ru.count("```bash") == en.count("```bash")


def test_parse_batch_response_fills_missing_ids():
    batch = [
        LineUnit("L00001", 1, "Привет."),
        LineUnit("L00002", 2, "Мир."),
    ]
    raw = json.dumps({"lines": [{"id": "L00001", "text": "Hello."}]})
    out = _parse_batch_response(raw, batch)
    assert out["L00001"] == "Hello."
    assert out["L00002"] == "Мир."


def test_parse_batch_response_invalid_json_returns_source_lines():
    batch = [LineUnit("L00003", 3, "Текст.")]
    out = _parse_batch_response("not json at all", batch)
    assert out["L00003"] == "Текст."


def test_systemd_tab_and_download_link_are_separate_units(fence_sample_ru: str):
    regions = refine_tab_regions(
        fence_sample_ru,
        analyze_document_structure(fence_sample_ru, source_is_russian=True),
    )
    units = [
        u
        for s in build_placeholder_segments(
            fence_sample_ru, regions, source_is_russian=True
        )
        if s.kind == "translate"
        for u in s.units
    ]
    labels = [u.source_line.strip() for u in units]
    assert "- С использованием systemd" in labels
    assert any("скачать из репозитория" in ln for ln in labels)
    assert not any(
        ln.startswith("- С использованием systemd[") for ln in labels
    )


def test_manual_tabs_tab_label_is_separate_unit():
    ru = (
        "Intro.\n\n"
        "{% list tabs group=manual-systemd %}\n\n"
        "- Вручную\n\n"
        "Текст.\n\n"
        "{% endlist %}\n\n"
        "Outro.\n"
    )
    regions = refine_tab_regions(
        ru, analyze_document_structure(ru, source_is_russian=True)
    )
    segs = build_placeholder_segments(ru, regions, source_is_russian=True)
    units = [u for s in segs if s.kind == "translate" for u in s.units]
    tab_units = [u for u in units if u.source_line.strip() == "- Вручную"]
    assert len(tab_units) == 1
    assert "Текст." in [u.source_line for u in units]


def test_assemble_preserves_indent():
    seg = build_placeholder_segments(
        "  hello\n", [analyze_document_structure("  hello\n")[0]]
    )[0]
    assert seg.kind == "translate"
    out = assemble_translate_segment(
        "  hello\n",
        seg,
        {"L00001": "world"},
    )
    assert out == "  world"


def test_file_translate_uses_masked_by_default():
    ru = "### Заголовок {#t}\n\nПривет.\n"
    with patch(
        "ydbdoc_review.masked_translate.translate_masked_chunk",
        side_effect=lambda _s, masked, **_k: masked.replace("Привет", "Hello"),
    ):
        out, llm = translate_text_with_plan(
            MagicMock(),
            source_path="x.md",
            source_text=ru,
            source_lang="Russian",
            target_lang="English",
        )
    assert "Привет" not in out
    assert "Hello" in out
    assert llm >= 1
