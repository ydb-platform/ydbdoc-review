"""F-048 contracts for text and Mermaid fence translation."""

from __future__ import annotations

from ydbdoc_review.parsing.front_matter import parse_front_matter
from ydbdoc_review.validation.fence_comments import translate_cyrillic_text_fences
from ydbdoc_review.validation.fence_integrity import fence_content_matches_source


def test_F048_text_output() -> None:
    source = (
        "```text\n"
        "$ ydb --database /local\n"
        "Result: 42\n"
        "Описание результата\n"
        "```\n"
    )

    translated = translate_cyrillic_text_fences(
        source,
        lambda body: "Result description" if body == "Описание результата" else body,
    )

    assert "$ ydb --database /local" in translated
    assert "Result: 42" in translated
    assert "Result description" in translated
    assert "Описание результата" not in translated


def test_F048_mermaid_labels() -> None:
    source = (
        "---\n"
        "title: Mermaid example\n"
        "vcsPath: docs/ru/example.md\n"
        "---\n\n"
        "```mermaid\n"
        "flowchart LR\n"
        "%%{init: {\"theme\": \"base\"}}%%\n"
        "  A[Короткая подпись] --> B[Финиш]\n"
        "  %% Обычный комментарий\n"
        "```\n"
    )
    target = (
        "---\n"
        "title: Mermaid example\n"
        "vcsPath: docs/ru/example.md\n"
        "---\n\n"
        "```mermaid\n"
        "flowchart LR\n"
        "%%{init: {\"theme\": \"base\"}}%%\n"
        "  A[This is a deliberately much longer label] --> B[Finished]\n"
        "  %% Ordinary comment\n"
        "```\n"
    )
    source_content = (
        "flowchart LR\n"
        "%%{init: {\"theme\": \"base\"}}%%\n"
        "  A[Короткая подпись] --> B[Финиш]\n"
        "  %% Обычный комментарий\n"
    )
    target_content = (
        "flowchart LR\n"
        "%%{init: {\"theme\": \"base\"}}%%\n"
        "  A[This is a deliberately much longer label] --> B[Finished]\n"
        "  %% Ordinary comment\n"
    )

    assert parse_front_matter(source.split("---\n", 2)[1]) == parse_front_matter(
        target.split("---\n", 2)[1]
    )
    assert fence_content_matches_source(
        source_content,
        target_content,
        fence_info="mermaid",
    )
    assert "A[This is a deliberately much longer label]" in target
    assert "--> B[Finished]" in target
    assert "%%{init: {\"theme\": \"base\"}}%%" in target
