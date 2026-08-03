"""§6.168: partial differential seed when full EN↔RU align fails."""

from __future__ import annotations

from textwrap import dedent

from ydbdoc_review.pipeline.qa import partial_align_translations_from_target
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.translation.differential import DifferentialTranslationAnalyzer
from ydbdoc_review.validation.homoglyphs import fix_russian_angle_placeholders_in_en_fences


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
    # Title + intro + Section A heading/body should seed; Extra may not;
    # Section B should seed from suffix.
    assert seeded
    assert len(seeded) >= 4
    # Must not claim seed for every RU segment (extra has no EN twin).
    assert len(seeded) < len(base_segs)


def test_differential_partial_seed_keeps_unchanged_en():
    ru_base = dedent(
        """\
        # G

        Old paragraph.

        ## Client certificate

        A **client certificate** is used with {{ ydb-short-name }}.
        """
    )
    # PR adds a sentence in Old paragraph only — plus an extra heading wedge that
    # breaks full align vs a slightly shorter EN (simulated by omitting Extra).
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
    # Client-certificate EN must be seeded, not wiped for full retranslate.
    cert_seeds = [
        t for t in plan.seeded_translations.values() if "client certificate" in t.lower()
    ]
    assert cert_seeds, plan.seeded_translations
    assert "⟦V" not in cert_seeds[0]


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
