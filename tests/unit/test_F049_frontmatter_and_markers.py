"""F-049: preserve service structures and publish leaked markers as RED."""

from __future__ import annotations

from types import SimpleNamespace

from ydbdoc_review.parsing.front_matter import (
    apply_front_matter_updates,
    parse_front_matter,
)
from ydbdoc_review.pipeline.publication import evaluate_publication_impact
from ydbdoc_review.pipeline.types import (
    FileTranslationResult,
    PairRunResult,
    PRTranslationResult,
    PublicationImpact,
)
from ydbdoc_review.validation.heuristics import check_unrestored_placeholders


def test_F049_preserve_yaml():
    raw = (
        "title: 'Заголовок'\n"
        "# keep this comment\n"
        "vcsPath: \"docs/ru/page.md\"\n"
        "editable: false\n"
        "description: |\n"
        "  Русское описание\n"
        "  Вторая строка\n"
        "custom: [one, two]\n"
    )

    translated = apply_front_matter_updates(
        raw,
        {"title": "English title", "description": "English description\n"},
    )

    assert translated == (
        "title: 'English title'\n"
        "# keep this comment\n"
        "vcsPath: \"docs/ru/page.md\"\n"
        "editable: false\n"
        "description: |\n"
        "  English description\n"
        "custom: [one, two]\n"
    )
    fields = parse_front_matter(translated)
    assert fields["vcsPath"] == "docs/ru/page.md"
    assert fields["editable"] is False
    assert fields["custom"] == ["one", "two"]


def test_F049_markers():
    translated_text = "The translated paragraph keeps its readable prose.\n"
    assert check_unrestored_placeholders(translated_text, target_lang="en") == []

    leaked = "The translated paragraph still contains ⟦C1⟧.\n"
    marker_messages = check_unrestored_placeholders(leaked, target_lang="en")
    assert marker_messages and marker_messages[0].startswith("unrestored_placeholder:")

    file_result = FileTranslationResult(
        file_path="docs/en/page.md",
        final_text=leaked,
        segments_count=1,
        verdict="blocked",
        prompt_version="test",
        heuristic_blocking=marker_messages,
    )
    result = PRTranslationResult(
        pair_results=[
            PairRunResult(
                plan=SimpleNamespace(
                    action="translate",
                    source_path="docs/ru/page.md",
                    target_path="docs/en/page.md",
                    source_lang="ru",
                    target_lang="en",
                ),
                source_text="Абзац.\n",
                target_text=leaked,
                file_result=file_result,
            )
        ]
    )

    assert evaluate_publication_impact(result) == PublicationImpact.PUBLISH_RED
