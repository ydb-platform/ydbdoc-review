"""§6.184: low-magnitude EN patch for tiny RU glossary-style additions."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.differential import (
    analyze_ru_diff,
    patch_en_with_added_translations,
    patch_en_with_source_added_autotitle_lines,
    prepare_differential_seed,
    slim_pending_for_low_magnitude_patch,
)


def test_pr_50904_autotitle_addition_preserves_unrelated_en_bytes():
    ru_base = dedent(
        """\
        # Концепции администрирования кластеров

        * [{#T}](./maintenance-without-downtime.md)

        * [{#T}](../backup-and-recovery/index.md)
        """
    )
    ru_pr = ru_base.replace(
        "* [{#T}](./maintenance-without-downtime.md)\n",
        "* [{#T}](./maintenance-without-downtime.md)\n* [{#T}](./node-authorization.md)\n",
    )
    en = dedent(
        """\
        # Concepts for Cluster Administration

        * [{#T}](./maintenance-without-downtime.md)

        * [{#T}](../backup-and-recovery.md)
        """
    )

    assert patch_en_with_source_added_autotitle_lines(ru_base, ru_pr, en) == en.replace(
        "* [{#T}](./maintenance-without-downtime.md)\n",
        "* [{#T}](./maintenance-without-downtime.md)\n* [{#T}](./node-authorization.md)\n",
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


def test_slim_pending_activates_even_when_pending_equals_changes():
    """Regression #49578: slim==pending must still patch, not reconstruct."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "fixtures" / "markdown_files"
    ru_base = (root / "ru/core/concepts/glossary.md").read_text(encoding="utf-8")
    en = (root / "en/core/concepts/glossary.md").read_text(encoding="utf-8")
    ru_pr = ru_base.replace(
        "### Board {#board}",
        "Подробнее — в разделе [Сервисы](architecture/metadata-services.md).\n\n### Board {#board}",
        1,
    )
    segs = extract_segments(parse_markdown(ru_pr))
    _strategy, _seeded, pending = prepare_differential_seed(
        pr_segments=segs,
        ru_pr_text=ru_pr,
        en_current_text=en,
        ru_base_text=ru_base,
    )
    # Pretend all unseeded segments were already filtered to the change set.
    analysis = analyze_ru_diff(ru_base, ru_pr)
    change_ids = analysis.added_segment_ids | analysis.modified_segment_ids
    pending_only_changes = [s for s in pending if s.id in change_ids]
    assert pending_only_changes
    slim = slim_pending_for_low_magnitude_patch(
        pending_only_changes, ru_base_text=ru_base, ru_pr_text=ru_pr
    )
    assert slim is not None
    slim_pending, slim_analysis = slim
    assert slim_analysis.change_magnitude < 0.05
    assert len(slim_pending) == len(pending_only_changes)


def test_slim_pending_keeps_only_added_for_tiny_glossary_edit():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "fixtures" / "markdown_files"
    ru_base = (root / "ru/core/concepts/glossary.md").read_text(encoding="utf-8")
    en = (root / "en/core/concepts/glossary.md").read_text(encoding="utf-8")
    # Mimic #45667: three new cross-links under StateStorage / Board / SchemeBoard.
    ru_pr = ru_base.replace(
        "### Board {#board}",
        "Подробнее — в разделе [Сервисы](architecture/metadata-services.md).\n\n### Board {#board}",
        1,
    )
    segs = extract_segments(parse_markdown(ru_pr))
    _strategy, _seeded, pending = prepare_differential_seed(
        pr_segments=segs,
        ru_pr_text=ru_pr,
        en_current_text=en,
        ru_base_text=ru_base,
    )
    slim = slim_pending_for_low_magnitude_patch(pending, ru_base_text=ru_base, ru_pr_text=ru_pr)
    assert slim is not None
    slim_pending, analysis = slim
    assert analysis.change_magnitude < 0.05
    assert 1 <= len(slim_pending) <= 5
    assert len(slim_pending) < len(pending)


def test_patch_en_replaces_plain_crossref_with_linked():
    """Main EN already has plain 'see … section'; RU adds a markdown link."""
    en = dedent(
        """\
        ### State Storage {#state-storage}

        **State Storage** is a distributed service.

        For more details about the StateStorage architecture and related \
subsystems, see the Metadata distribution services section.

        ### Board {#board}

        **Board** stores key-value metadata.
        """
    )
    ru = dedent(
        """\
        ### Хранилище состояния {#state-storage}

        **Хранилище состояния** — сервис.

        Подробнее об устройстве StateStorage — в разделе \
[Сервисы распространения метаданных](architecture/metadata-services.md).

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
    change_ids = analysis.added_segment_ids | analysis.modified_segment_ids
    added_id = next(iter(change_ids))
    linked = (
        "For more details about the StateStorage architecture and related "
        "subsystems, see the section "
        "[Metadata distribution services](architecture/metadata-services.md)."
    )
    out = patch_en_with_added_translations(
        en,
        pr_segments=segs,
        translations={added_id: linked},
        added_segment_ids=analysis.added_segment_ids,
        modified_segment_ids=analysis.modified_segment_ids,
    )
    assert out.count("Metadata distribution services") == 1
    assert "[Metadata distribution services](architecture/metadata-services.md)" in out
    assert out.index("State Storage") < out.index("Metadata distribution")
    assert out.index("Metadata distribution") < out.index("### Board")


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
