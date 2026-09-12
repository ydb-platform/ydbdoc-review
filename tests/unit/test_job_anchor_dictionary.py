"""Job-level Cyrillic→EN anchor dictionary (REQUIREMENTS §8 / plan P3)."""

from __future__ import annotations

from ydbdoc_review.harness.render import render_with_translations
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.types import SegmentKind
from ydbdoc_review.validation.yfm_anchor import JobAnchorDictionary


def test_job_anchor_dictionary_two_files_share_one_en_for_same_ru():
    """Two files, one shared RU Cyrillic anchor → one EN value for the job."""
    dictionary = JobAnchorDictionary()

    ru_a = "## Поля {#поля}\n\nОписание полей.\n"
    doc_a = parse_markdown(ru_a)
    segments_a = extract_segments(doc_a)
    heading_a = next(s for s in segments_a if s.kind == SegmentKind.HEADING)
    prose_a = next(s for s in segments_a if s.kind == SegmentKind.PARAGRAPH)
    text_a = render_with_translations(
        doc_a,
        segments_a,
        {heading_a.id: "Fields", prose_a.id: "Field descriptions."},
        target_lang="en",
        job_anchor_dictionary=dictionary,
    )
    assert "{#поля}" not in text_a
    assert "{#fields}" in text_a
    assert dictionary.as_map() == {"поля": "fields"}

    # Re-resolving the same RU key must not invent a second EN value.
    assert dictionary.lookup_or_insert("поля", "Completely Different Heading") == "fields"

    ru_b = "См. [поля](a.md#поля) и [ещё](#поля).\n"
    doc_b = parse_markdown(ru_b)
    segments_b = extract_segments(doc_b)
    translations_b = {
        seg.id: seg.text.replace("См.", "See")
        .replace("поля", "fields")
        .replace("ещё", "again")
        .replace(" и ", " and ")
        for seg in segments_b
    }
    text_b = render_with_translations(
        doc_b,
        segments_b,
        translations_b,
        target_lang="en",
        job_anchor_dictionary=dictionary,
    )
    assert "a.md#fields" in text_b
    assert "(#fields)" in text_b
    assert "#поля" not in text_b
    assert dictionary.as_map() == {"поля": "fields"}


def test_job_anchor_dictionary_ascii_stays_byte_identical():
    dictionary = JobAnchorDictionary()
    assert dictionary.lookup_or_insert("ldap-tls", "TLS settings") == "ldap-tls"
    assert dictionary.as_map() == {}
