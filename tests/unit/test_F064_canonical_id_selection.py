"""F-064: canonical heading IDs are stable and reproducible."""

from __future__ import annotations

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.validation.yfm_anchor import (
    JobAnchorDictionary,
    apply_job_anchors_to_document,
)


def test_F064_priority_collision() -> None:
    source = parse_markdown(
        "## Existing {#stable-id}\n\n"
        "## First {#первый}\n\n"
        "## Second {#второй}\n"
    )
    target = parse_markdown(
        "## Existing {#renamed-by-translation}\n\n"
        "## Same title {#agreed-id}\n\n"
        "## Same title\n"
    )

    dictionary = JobAnchorDictionary()
    apply_job_anchors_to_document(target, dictionary=dictionary, source_doc=source)

    assert [heading.anchor for heading in target.children if heading.anchor] == [
        "stable-id",
        "agreed-id",
        "same-title",
    ]
    assert dictionary.lookup_or_insert("первый", "Different title") == "agreed-id"
    assert dictionary.lookup_or_insert("третий", "Same title") == "same-title-2"


def test_F064_later_section() -> None:
    source = parse_markdown("## First {#раздел}\n")
    target = parse_markdown("## First {#agreed-section}\n")
    dictionary = JobAnchorDictionary()

    apply_job_anchors_to_document(target, dictionary=dictionary, source_doc=source)
    first_id = target.children[0].anchor

    later_source = parse_markdown("## First {#раздел}\n\n## First {#поздний}\n")
    later_target = parse_markdown("## First {#agreed-section}\n\n## First\n")
    apply_job_anchors_to_document(
        later_target,
        dictionary=dictionary,
        source_doc=later_source,
    )

    assert first_id == "agreed-section"
    assert later_target.children[0].anchor == first_id
    assert later_target.children[1].anchor == "first"
    assert "{#agreed-section}" in render_markdown(later_target)
