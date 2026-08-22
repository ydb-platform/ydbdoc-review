"""§6.185: skip full verify realign on large files (glossary hang)."""

from __future__ import annotations

from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.harness.steps import FinalizeEnStep, RoundTripStep
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary


def test_verify_repairs_legacy_layout_before_round_trip_gate(monkeypatch):
    source = "- item\n\n    ```python\n    source()\n      ```\n"
    target = "- item\n\n  ```python\n  translated()\n    ```\n  ```\n"
    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/core/legacy.md",
        raw_source_text=source,
        source_text=source,
        existing_target_text=target,
        translated_text=target,
        segments=extract_segments(parse_markdown(source)),
        source_doc=parse_markdown(source),
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    ctx = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
    )

    seen: list[str] = []

    def _gate(_segments, text):
        seen.append(text)
        return {}, None

    def _structural_repair(current_state, _ctx):
        current_state.translated_text += "```\n"

    monkeypatch.setattr(
        "ydbdoc_review.harness.steps._apply_en_structural_repair",
        _structural_repair,
    )
    monkeypatch.setattr("ydbdoc_review.harness.steps.gate_round_trip", _gate)
    RoundTripStep().run(state, ctx)
    assert len(seen) == 1
    assert seen[0].count("```") == source.count("```")
    assert "    ```python" in seen[0]
    assert "      ```" in seen[0]


def test_verify_finalize_keeps_en_body_ref_but_passes_ru_layout_ref(monkeypatch):
    source = "- item\n\n    ```python\n    source()\n      ```\n"
    target = "- item\n\n  ```python\n  translated()\n    ```\n  ```\n"
    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/core/legacy.md",
        raw_source_text=source,
        source_text=source,
        existing_target_text=target,
        translated_text=target,
        fence_reference_text=target,
        segments=extract_segments(parse_markdown(source)),
        source_doc=parse_markdown(source),
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    ctx = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
    )
    seen: dict[str, str] = {}

    def _finalize(text, fence_ref, **kwargs):
        seen["fence_ref"] = fence_ref
        seen["layout_ref"] = kwargs["layout_source_text"]
        return text

    monkeypatch.setattr("ydbdoc_review.harness.steps.finalize_en_target", _finalize)
    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.repair_missing_includes",
        lambda _source, text, **_kwargs: text,
    )
    FinalizeEnStep().run(state, ctx)
    assert seen == {"fence_ref": target, "layout_ref": source}


def test_verify_realign_skips_full_retranslate_for_large_files(monkeypatch):
    """449-segment glossary must not call translate_segments during verify."""
    # Build a RU with many short paragraphs so segment count exceeds the cap.
    ru_parts = [f"## H{i} {{#h{i}}}\n\nPara {i}.\n" for i in range(90)]
    ru = "# Title\n\n" + "\n".join(ru_parts)
    en = "# Title\n\nDifferent structure only.\n"
    segs = extract_segments(parse_markdown(ru))
    assert len(segs) > 80

    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/core/dev/large-guide.md",
        raw_source_text=ru,
        source_text=ru,
        existing_target_text=en,
        translated_text=en,
        segments=segs,
        source_doc=parse_markdown(ru),
    )
    cfg = load_config(env={"YDBDOC_YC_FOLDER_ID": "b1", "YDBDOC_YC_API_KEY": "k"})
    ctx = HarnessContext.from_options(
        MagicMock(),
        glossary=load_glossary(),
        config=cfg,
    )

    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("translate_segments must not run for large realign")

    monkeypatch.setattr("ydbdoc_review.harness.steps.translate_segments", _boom)
    RoundTripStep().run(state, ctx)
    assert called["n"] == 0
    assert state.segment_alignment_error
    assert any("verify_realign_skipped" in w for w in state.finalize_warnings)
    assert state.translated_text == en
