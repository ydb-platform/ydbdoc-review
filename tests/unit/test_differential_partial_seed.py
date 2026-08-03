"""§6.168–§6.170: partial differential seed when full EN↔RU align fails."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.pipeline.qa import partial_align_translations_from_target
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.translation.differential import DifferentialTranslationAnalyzer
from ydbdoc_review.validation.homoglyphs import fix_russian_angle_placeholders_in_en_fences
from ydbdoc_review.validation.markers import placeholders_match
from ydbdoc_review.harness.render import render_with_translations
from ydbdoc_review.validation.heuristics import check_unrestored_placeholders


def test_partial_align_seeds_prefix_and_suffix_around_wedge():
    """RU has one extra segment in the middle; EN keeps older structure."""
    ru_base = dedent(
        """\
        # Title

        Intro paragraph.

        ## Section A

        Body A.

        ## Extra only on RU

        Extra body.

        ## Section B

        Body B.
        """
    )
    en = dedent(
        """\
        # Title

        Intro paragraph.

        ## Section A

        Body A.

        ## Section B

        Body B.
        """
    )
    base_segs = extract_segments(parse_markdown(ru_base))
    seeded = partial_align_translations_from_target(base_segs, en)
    assert seeded
    assert len(seeded) >= 4
    assert len(seeded) < len(base_segs)


def test_partial_align_lcs_seeds_past_early_kind_wedge():
    """#48764: early heading/paragraph drift must not drop the whole suffix."""
    ru_base = dedent(
        """\
        # Title

        Intro.

        ## Early

        Early body.

        ## Mid

        Mid body.

        ## Late

        Late body that must stay seeded.
        """
    )
    en = dedent(
        """\
        # Title

        Intro.

        Early body as paragraph without heading.

        ## Mid

        Mid body.

        ## Late

        Late body that must stay seeded.
        """
    )
    base_segs = extract_segments(parse_markdown(ru_base))
    seeded = partial_align_translations_from_target(base_segs, en)
    late = [t for t in seeded.values() if "Late body that must stay seeded" in t]
    assert late, f"expected Late body seeded, got {len(seeded)}/{len(base_segs)}: {seeded!r}"
    assert len(seeded) >= len(base_segs) - 2


def test_partial_align_rejects_placeholder_mismatched_lcs_pairs():
    """#48773: kind-only LCS must not seed EN with foreign ⟦…⟧ into a plain RU slot."""
    ru = dedent(
        """\
        # Auth

        Supported modes:

        Anonymous.
        """
    )
    # Same kinds, shifted content: EN "Supported modes" sentence has a variable.
    en = dedent(
        """\
        # Auth

        An authentication client accessing {{ ydb-short-name }}.

        Anonymous.
        """
    )
    ru_segs = extract_segments(parse_markdown(ru))
    seeded = partial_align_translations_from_target(ru_segs, en)
    for seg in ru_segs:
        if seg.id in seeded:
            assert placeholders_match(seg.text, seeded[seg.id]), (
                seg.id,
                seg.text,
                seeded[seg.id],
            )
    # "Supported modes:" (no ph) must not receive the EN sentence with ⟦V1⟧.
    modes = next(s for s in ru_segs if "Supported" in s.text)
    assert modes.id not in seeded or "⟦V" not in seeded[modes.id]

    translations = dict(seeded)
    for s in ru_segs:
        translations.setdefault(s.id, s.text)
    out = render_with_translations(parse_markdown(ru), ru_segs, translations)
    assert check_unrestored_placeholders(out, target_lang="en") == []
    assert "⟦" not in out


def test_differential_partial_seed_keeps_unchanged_en():
    ru_base = dedent(
        """\
        # G

        Old paragraph.

        ## Client certificate

        A **client certificate** is used with {{ ydb-short-name }}.
        """
    )
    ru_pr = dedent(
        """\
        # G

        Old paragraph with a small RU edit.

        ## Wedge

        Wedge text.

        ## Client certificate

        A **client certificate** is used with {{ ydb-short-name }}.
        """
    )
    en = dedent(
        """\
        # G

        Old paragraph.

        ## Client certificate

        A **client certificate** is used with {{ ydb-short-name }}.
        """
    )
    plan = DifferentialTranslationAnalyzer().plan_translation(
        ru_pr_text=ru_pr,
        en_current_text=en,
        ru_base_text=ru_base,
    )
    cert_seeds = [
        t for t in plan.seeded_translations.values() if "client certificate" in t.lower()
    ]
    assert cert_seeds, plan.seeded_translations
    assert "⟦V" not in cert_seeds[0] or placeholders_match(
        next(
            s.text
            for s in extract_segments(parse_markdown(ru_pr))
            if "client certificate" in s.text.lower()
        ),
        cert_seeds[0],
    )


def test_angle_placeholders_client_cert_yaml():
    text = dedent(
        """\
        ```yaml
          default_group: <SID по умолчанию>
            - member_groups: <массив SID>
                suffixes: <массив разрешенных суффиксов>
                values: <массив допустимых значений>
                - short_name: <имя компонента Subject Name>
        ```
        """
    )
    fixed = fix_russian_angle_placeholders_in_en_fences(text)
    assert "<default SID>" in fixed
    assert "<SID array>" in fixed
    assert "<array of allowed suffixes>" in fixed
    assert "<array of allowed values>" in fixed
    assert "<Subject Name component name>" in fixed
    assert "по умолчанию" not in fixed
    assert "массив" not in fixed
