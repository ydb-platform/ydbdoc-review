"""F-065: a Cyrillic RU anchor is migrated narrowly and consistently."""

from __future__ import annotations

from ydbdoc_review.harness.render import render_with_translations
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.reporting.provenance_drift import _relations
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.types import SegmentKind
from ydbdoc_review.validation.yfm_anchor import JobAnchorDictionary


def test_F065_ru_migration() -> None:
    source = (
        "## Описание полей {#поля}\n\n"
        "См. [поля](#поля) и [другой раздел](other.md#other).\n"
    )
    document = parse_markdown(source)
    segments = extract_segments(document)
    translations = {
        segment.id: segment.text.replace("Описание полей", "Field descriptions")
        .replace("См.", "See")
        .replace("поля", "fields")
        for segment in segments
    }

    output = render_with_translations(
        document,
        segments,
        translations,
        target_lang="en",
        job_anchor_dictionary=JobAnchorDictionary(),
    )

    assert "{#поля}" not in output
    assert "{#field-descriptions}" in output
    assert "(#field-descriptions)" in output
    assert "other.md#other" in output


def test_F065_narrow_diff() -> None:
    source = (
        "## Раздел {#раздел}\n\n"
        "RU prose stays byte-identical. See [other](other.md#other).\n"
    )
    document = parse_markdown(source)
    segments = extract_segments(document)
    heading = next(segment for segment in segments if segment.kind == SegmentKind.HEADING)
    prose = next(segment for segment in segments if segment.kind == SegmentKind.PARAGRAPH)
    output = render_with_translations(
        document,
        segments,
        {heading.id: "Section", prose.id: prose.text},
        target_lang="en",
        job_anchor_dictionary=JobAnchorDictionary(),
    )

    assert "RU prose stays byte-identical." in prose.text
    assert "{#section}" in output
    source_relations = _relations("docs/ru/page.md", source)
    output_relations = _relations("docs/ru/page.md", output)
    assert {r for r in output_relations if r.kind != "anchor"} == {
        r for r in source_relations if r.kind != "anchor"
    }
    assert any(r.kind == "anchor" and r.target == "#section" for r in output_relations)
