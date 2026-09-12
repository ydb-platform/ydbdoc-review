"""F-070: EN→RU anchor transfer and source-scope blocking."""

from __future__ import annotations

from ydbdoc_review.harness.render import render_with_translations
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.analyze import PairContent, plan_pair_heuristic
from ydbdoc_review.pipeline.pairs import DocPair
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.types import SegmentKind
from ydbdoc_review.validation.heuristics import run_file_heuristics_classified
from ydbdoc_review.validation.href_parity import check_heading_anchor_parity


def test_F070_canonical_en() -> None:
    """A canonical EN ID is copied to RU while EN remains byte-identical."""
    source = "# Stable heading {#stable-id}\n\nBody.\n"
    source_doc = parse_markdown(source)
    segments = extract_segments(source_doc)
    translations = {
        segment.id: (
            "Стабильный заголовок" if segment.kind is SegmentKind.HEADING else "Текст."
        )
        for segment in segments
    }

    target = render_with_translations(
        source_doc,
        segments,
        translations,
        target_lang="ru",
    )

    assert source == "# Stable heading {#stable-id}\n\nBody.\n"
    assert "# Стабильный заголовок {#stable-id}" in target
    assert check_heading_anchor_parity(source, target, source_lang="en", target_lang="ru") == []


def test_F070_mixed_scope() -> None:
    """A neighbouring RU→EN permission cannot authorize a bad EN source ID."""
    ru_to_en = PairContent(
        pair=DocPair(
            ru_path="ydb/docs/ru/neighbor.md",
            en_path="ydb/docs/en/neighbor.md",
            ru_changed=True,
        ),
        ru_text="# Сосед {#сосед}\n",
        en_text="# Neighbor {#neighbor}\n",
    )
    en_to_ru = PairContent(
        pair=DocPair(
            ru_path="ydb/docs/ru/bad.md",
            en_path="ydb/docs/en/bad.md",
            en_changed=True,
        ),
        en_text="# Bad source {#bad id}\n",
        ru_text="# Плохой источник {#bad-id}\n",
    )

    assert plan_pair_heuristic(ru_to_en).source_lang == "ru"
    assert plan_pair_heuristic(en_to_ru).source_lang == "en"
    classified = run_file_heuristics_classified(
        en_to_ru.en_text or "",
        en_to_ru.ru_text or "",
        normalized_source_text=en_to_ru.en_text or "",
        source_lang="en",
        target_lang="ru",
    )
    assert any(message.startswith("anchor_parity:") for message in classified.blocking)
    assert any("fix the EN source separately" in message for message in classified.blocking)
