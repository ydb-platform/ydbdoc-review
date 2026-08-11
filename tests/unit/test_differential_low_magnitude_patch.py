"""§6.184: low-magnitude EN patch for tiny RU glossary-style additions."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.differential import (
    analyze_ru_diff,
    patch_en_with_added_translations,
    prepare_differential_seed,
    slim_pending_for_low_magnitude_patch,
)


def test_glossary_zero_diff_seeds_most_segments():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "fixtures" / "markdown_files"
    ru = (root / "ru/core/concepts/glossary.md").read_text(encoding="utf-8")
    en = (root / "en/core/concepts/glossary.md").read_text(encoding="utf-8")
    segs = extract_segments(parse_markdown(ru))
    _strategy, seeded, pending = prepare_differential_seed(
        pr_segments=segs,
        ru_pr_text=ru,
        en_current_text=en,
        ru_base_text=ru,
    )
    assert len(seeded) > len(pending)
    assert len(pending) < 100


def test_slim_pending_keeps_only_added_for_tiny_glossary_edit():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "fixtures" / "markdown_files"
    ru_base = (root / "ru/core/concepts/glossary.md").read_text(encoding="utf-8")
    en = (root / "en/core/concepts/glossary.md").read_text(encoding="utf-8")
    # Mimic #45667: three new cross-links under StateStorage / Board / SchemeBoard.
    ru_pr = ru_base.replace(
        "### Board {#board}",
        "Подробнее — в разделе [Сервисы](architecture/metadata-services.md).\n\n"
        "### Board {#board}",
        1,
    )
    segs = extract_segments(parse_markdown(ru_pr))
    _strategy, _seeded, pending = prepare_differential_seed(
        pr_segments=segs,
        ru_pr_text=ru_pr,
        en_current_text=en,
        ru_base_text=ru_base,
    )
    slim = slim_pending_for_low_magnitude_patch(
        pending, ru_base_text=ru_base, ru_pr_text=ru_pr
    )
    assert slim is not None
    slim_pending, analysis = slim
    assert analysis.change_magnitude < 0.05
    assert 1 <= len(slim_pending) <= 5
    assert len(slim_pending) < len(pending)


def test_patch_en_inserts_after_anchor():
    en = dedent(
        """\
        ### State Storage {#state-storage}

        **State Storage** is a distributed service.

        ### Board {#board}

        **Board** stores key-value metadata.
        """
    )
    ru = dedent(
        """\
        ### Хранилище состояния {#state-storage}

        **Хранилище состояния** — сервис.

        Подробнее — в разделе [Сервисы](architecture/metadata-services.md).

        ### Board {#board}

        **Board** хранит метаданные.
        """
    )
    segs = extract_segments(parse_markdown(ru))
    analysis = analyze_ru_diff(
        dedent(
            """\
            ### Хранилище состояния {#state-storage}

            **Хранилище состояния** — сервис.

            ### Board {#board}

            **Board** хранит метаданные.
            """
        ),
        ru,
    )
    assert analysis.added_segment_ids or analysis.modified_segment_ids
    change_ids = analysis.added_segment_ids | analysis.modified_segment_ids
    added_id = next(iter(change_ids))
    translations = {
        added_id: (
            "For more details, see the section "
            "[Metadata services](architecture/metadata-services.md)."
        )
    }
    out = patch_en_with_added_translations(
        en,
        pr_segments=segs,
        translations=translations,
        added_segment_ids=analysis.added_segment_ids,
        modified_segment_ids=analysis.modified_segment_ids,
    )
    assert "Metadata services" in out
    assert out.index("Metadata services") < out.index("### Board")
