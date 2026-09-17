"""F-069: anchor conflicts fail closed and remain RED-visible."""

from __future__ import annotations

from ydbdoc_review.harness.render import render_with_translations
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.publication import evaluate_publication_impact
from ydbdoc_review.pipeline.types import FinalTreeBlocker, PRTranslationResult, PublicationImpact
from ydbdoc_review.validation.fragment_repair import _remap_fragment_via_ru_en_pages
from ydbdoc_review.validation.yfm_anchor import (
    JobAnchorDictionary,
    build_heading_anchor_map,
)


def test_F069_conflicts() -> None:
    dictionary = JobAnchorDictionary()
    assert dictionary.lookup_or_insert("первый", "First", preferred_anchor="shared") == "shared"
    assert dictionary.lookup_or_insert("второй", "Second", preferred_anchor="shared") == "shared-2"
    assert dictionary.lookup_or_insert("первый", "Changed") == "shared"

    ru = parse_markdown("## One {#one}\n\n## Two {#two}\n")
    en = parse_markdown("## One {#one}\n")
    assert build_heading_anchor_map(ru, en) == {}
    assert _remap_fragment_via_ru_en_pages(
        "missing",
        "## Missing {#missing}\n\n## Another {#another}\n",
        "## Other\n",
    ) is None


def test_F069_partial_migration() -> None:
    blocker = FinalTreeBlocker(
        code="en_link_target",
        path="docs/en/page.md",
        message="legacy alias belongs to another section; keep old address",
    )
    result = PRTranslationResult(final_tree_blockers=[blocker])

    assert result.final_tree_blockers[0].code == "en_link_target"
    assert evaluate_publication_impact(result) == PublicationImpact.PUBLISH_RED
    output = render_with_translations(
        parse_markdown("## Section {#section}\n"),
        [],
        {},
        target_lang="en",
    )
    assert "{#section}" in output
