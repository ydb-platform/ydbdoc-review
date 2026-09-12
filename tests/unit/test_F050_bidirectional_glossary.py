"""F-050: glossary prompts and deterministic checks work RU↔EN."""

from __future__ import annotations

from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.glossary import Glossary, GlossaryEntry
from ydbdoc_review.translation.prompts import (
    build_critic_messages,
    build_translate_messages,
)
from ydbdoc_review.validation.glossary_terms import check_glossary_term_violations


def _segment(text: str) -> Segment:
    return Segment(
        id="s0001",
        kind=SegmentKind.PARAGRAPH,
        path=["Body"],
        text=text,
        placeholders=[],
        ast_path=[0],
    )


def _glossary() -> Glossary:
    return Glossary(
        entries=[
            GlossaryEntry(
                ru="планшет",
                en="tablet",
                aliases_ru=["планшета"],
                context="YDB storage shard",
            ),
            GlossaryEntry(term="YDB", do_not_translate=True),
        ]
    )


def test_F050_both_directions():
    glossary = _glossary()
    ru_to_en = build_translate_messages(
        batch=type("Batch", (), {"index": 0, "segments": [_segment("планшета")]})(),
        glossary=glossary,
        file_path="docs/ru/page.md",
        source_lang="ru",
        target_lang="en",
    )
    en_to_ru = build_critic_messages(
        source_text="The tablet stores data.",
        translated_text="Планшет хранит данные.",
        segments=[_segment("tablet")],
        glossary=glossary,
        file_path="docs/en/page.md",
        source_lang="en",
        target_lang="ru",
    )
    ru_prompt = str(ru_to_en[0]["content"])
    critic_prompt = str(en_to_ru[1]["content"])
    assert "aliases_ru" in ru_prompt
    assert "YDB storage shard" in ru_prompt
    assert "tablet" in critic_prompt

    assert check_glossary_term_violations(
        "The планшета stores data.",
        target_lang="en",
        source_lang="ru",
        glossary=glossary,
    )
    assert check_glossary_term_violations(
        "Tablet хранит данные.",
        target_lang="ru",
        source_lang="en",
        glossary=glossary,
    )
    assert check_glossary_term_violations(
        "Планшет хранит данные.",
        target_lang="ru",
        source_lang="en",
        glossary=glossary,
    ) == []


def test_F050_required_term():
    glossary = _glossary()
    violation = check_glossary_term_violations(
        "The планшета stores data.",
        target_lang="en",
        glossary=glossary,
    )
    assert violation and violation[0].startswith("glossary_violation:")
    assert "tablet" in violation[0]

    # Technical / explicitly non-translatable terms are not false defects.
    assert check_glossary_term_violations(
        "YDB uses SQL.",
        target_lang="ru",
        source_lang="en",
        glossary=Glossary(
            entries=[
                GlossaryEntry(term="YDB", do_not_translate=True),
                GlossaryEntry(term="SQL", do_not_translate=True),
            ]
        ),
    ) == []
