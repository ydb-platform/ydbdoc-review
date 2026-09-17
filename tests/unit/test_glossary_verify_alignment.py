"""§6.186: glossary verify skips structural alignment gate."""

from __future__ import annotations

from unittest.mock import MagicMock

from ydbdoc_review.config.loader import load_config
from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.harness.steps import RoundTripStep
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import load_glossary


def test_glossary_verify_clears_alignment_error(monkeypatch):
    ru = "# Glossary\n\n## Actor {#actor}\n\nText.\n\n## Tablet {#tablet}\n\nMore.\n"
    en = "# Glossary\n\nDifferent.\n"
    segs = extract_segments(parse_markdown(ru))
    state = FileRunState(
        mode="verify",
        file_path="ydb/docs/ru/core/concepts/glossary.md",
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

    def _boom(*_a, **_k):
        raise AssertionError("translate_segments must not run for glossary verify")

    monkeypatch.setattr(
        "ydbdoc_review.harness.steps.translate_segments", _boom
    )
    RoundTripStep().run(state, ctx)
    assert state.segment_alignment_error is None
    assert any(
        "glossary_verify_alignment_skipped" in w for w in state.finalize_warnings
    )
