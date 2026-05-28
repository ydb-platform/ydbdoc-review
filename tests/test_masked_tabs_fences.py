"""Tabs fences must not be sent to the LLM as raw ``` markdown."""

from unittest.mock import MagicMock, patch

from ydbdoc_review.annotated_translate import refine_tab_regions
from ydbdoc_review.document_mask import MaskRegistry
from ydbdoc_review.document_structure import analyze_document_structure
from ydbdoc_review.masked_translate import (
    MaskedTranslateSegment,
    build_masked_segments,
    translate_with_mask,
)
from ydbdoc_review.placeholder_translate import CopySegment


def _tabs_with_bash_fixture() -> str:
    return (
        "Intro.\n\n"
        "{% list tabs %}\n\n"
        "- JSON\n\n"
        "Выполните запрос:\n\n"
        "  ```bash\n"
        "  {{ ydb-cli }} sql -s 'SELECT 1'\n"
        "  ```\n\n"
        "  ```text\n"
        '{"a":1}\n'
        "  ```\n\n"
        "- CSV\n\n"
        "Текст CSV.\n\n"
        "{% endlist %}\n"
    )


def test_refine_tab_regions_splits_inner_fences():
    text = _tabs_with_bash_fixture()
    regions = refine_tab_regions(
        text, analyze_document_structure(text, source_is_russian=True)
    )
    assert any(r.kind == "fence" and r.action == "copy_verbatim" for r in regions)
    assert not any(r.action == "translate_tabs" for r in regions)


def test_build_masked_segments_copy_fences_not_in_masked_prose():
    text = _tabs_with_bash_fixture()
    regions = refine_tab_regions(
        text, analyze_document_structure(text, source_is_russian=True)
    )
    reg = MaskRegistry()
    segs = build_masked_segments(text, regions, reg, source_is_russian=True)
    copy_fence = [s for s in segs if isinstance(s, CopySegment) and "```" in s.text]
    assert copy_fence, "expected copy segments for fenced blocks"
    for seg in segs:
        if not isinstance(seg, MaskedTranslateSegment):
            continue
        assert "```" not in seg.masked_text


def test_translate_with_mask_llm_never_sees_raw_backticks():
    text = _tabs_with_bash_fixture()
    seen: list[str] = []

    def _capture(_s, masked: str, **_k) -> str:
        seen.append(masked)
        return masked.replace("Выполните", "Run").replace("Текст CSV", "CSV text")

    with patch(
        "ydbdoc_review.masked_translate.translate_masked_chunk",
        side_effect=_capture,
    ):
        en, _ = translate_with_mask(
            MagicMock(),
            source_path="tabs.md",
            source_text=text,
            source_lang="Russian",
            target_lang="English",
        )
    assert seen
    for payload in seen:
        assert "```" not in payload
    assert "```bash" in en
    assert "SELECT 1" in en
